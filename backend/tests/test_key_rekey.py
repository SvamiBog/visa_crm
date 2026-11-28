"""A new data key for every workspace after a leak of VAULT_MASTER_KEY (backlog S16, audit L6).

A rotation of the master key only wraps the same workspace keys again: whoever kept the old
master key and a copy of the database from before it still opens every password and file
written after it. The leak response gives each workspace a new key, encrypts its vault
passwords and stored files again and turns two-factor login off for everyone."""

import asyncio
import base64
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cli import rekey_after_leak
from app.core import storage
from app.core.crypto import decrypt, generate_master_key
from app.core.db import scope_to_system, scope_to_tenant
from app.core.documents import object_key as document_key
from app.core.documents import open_document
from app.core.templates import object_key as template_key
from app.core.templates import open_template
from app.core.vault import (
    decrypt_for_tenant,
    encrypt_for_tenant,
    get_tenant_key,
    rekey_workspace,
    tenant_key_aad,
)
from app.models import VaultEntry
from tests.support.helpers import CLOCK, MakeUser, login, login_first_step
from tests.test_documents import PDF
from tests.test_documents import upload as upload_document
from tests.test_templates import LETTER, turn_on
from tests.test_templates import upload as upload_template
from tests.test_tenant_key import _until_one_waits_for_a_lock
from tests.test_vault import contact, entry, reveal, system


def _raw(master_key: str) -> bytes:
    return base64.b64decode(master_key)


async def _stored_key(owner_engine: AsyncEngine, tenant_id: uuid.UUID | None) -> bytes:
    async with owner_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT vault_key_encrypted FROM tenants WHERE id = :id"), {"id": tenant_id}
        )


async def _password(owner_engine: AsyncEngine, entry_id: str) -> bytes:
    async with owner_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT password_encrypted FROM vault_entries WHERE id = :id"), {"id": entry_id}
        )


async def _updated_at(owner_engine: AsyncEngine, entry_id: str):
    async with owner_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT updated_at FROM vault_entries WHERE id = :id"), {"id": entry_id}
        )


async def _account(client: AsyncClient, contact_id: str, password: str) -> dict:
    vfs = await system(client, name=f"VFS {uuid.uuid4().hex[:6]}")
    return await entry(
        client,
        system_id=vfs["id"],
        contact_id=contact_id,
        login=f"{uuid.uuid4().hex[:8]}@mail.by",
        email_owner="specialist",
        password=password,
        created_manually=True,
    )


async def test_after_a_leak_the_old_key_and_a_copy_from_before_open_nothing_stored(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, master_key
):
    leaked, new = generate_master_key(), generate_master_key()
    master_key(leaked)
    tenant_id = await make_user("anna@example.com")
    assert tenant_id is not None
    await login(client, "anna@example.com")
    anna = await contact(client)
    before = await _account(client, anna, "Before-1")
    document = (await upload_document(client, anna)).json()
    document_id = uuid.UUID(document["id"])
    copy = await _stored_key(owner_engine, tenant_id)  # what whoever took the key kept
    changed_at = await _updated_at(owner_engine, before["id"])

    master_key(new, previous=leaked)
    await rekey_after_leak()
    master_key(new)

    # A new key is not a change of the account.
    assert await _updated_at(owner_engine, before["id"]) == changed_at
    client.cookies.clear()
    assert (await login(client, "anna@example.com")).status_code == 200
    after = await _account(client, anna, "After-2")
    # The new key reads what was there and what came after.
    assert (await reveal(client, before["id"])).json() == {"password": "Before-1"}
    assert (await reveal(client, after["id"])).json() == {"password": "After-2"}
    download = await client.get(f"/api/contacts/{anna}/documents/{document_id}")
    assert download.status_code == 200 and download.content == PDF

    # The old master key still opens the old copy of the workspace key...
    old_key = decrypt(_raw(leaked), copy, tenant_key_aad(tenant_id))
    # ...but nothing stored now: neither a password saved after the response, nor one from
    # before it, nor the file, nor the workspace key itself.
    for account in (after, before):
        blob = await _password(owner_engine, account["id"])
        with pytest.raises(InvalidTag):
            decrypt_for_tenant(old_key, tenant_id, uuid.UUID(account["id"]), blob)
    stored_file = storage.MEMORY[document_key(tenant_id, document_id)]
    with pytest.raises(InvalidTag):
        open_document(old_key, tenant_id, document_id, stored_file)
    with pytest.raises(InvalidTag):
        decrypt(_raw(leaked), await _stored_key(owner_engine, tenant_id), tenant_key_aad(tenant_id))


async def test_the_leak_response_turns_two_factor_off_and_signs_everyone_out(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, master_key
):
    # The old master key opened every authenticator secret, and a recovery code is no
    # better: everyone sets two-factor login up again. Audit h02.8: a login left at its
    # second step ends too, and does not fail later.
    leaked, new = generate_master_key(), generate_master_key()
    master_key(leaked)
    await make_user("root@example.com", role="admin")
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    halfway = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login_first_step(halfway, "root@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO user_recovery_codes (id, user_id, code_hash) "
                "SELECT gen_random_uuid(), id, md5(email) FROM users"
            )
        )

    master_key(new, previous=leaked)
    await rekey_after_leak()
    master_key(new)

    assert (await client.get("/api/auth/me")).status_code == 401
    verify = await halfway.post("/api/auth/login/verify", json={"code": CLOCK.next_code()})
    assert verify.json() == {"detail": "login_expired"}
    await halfway.aclose()
    async with owner_engine.connect() as conn:
        left = await conn.scalar(
            text(
                "SELECT count(*) FROM users WHERE totp_secret_encrypted IS NOT NULL "
                "OR totp_enabled_at IS NOT NULL"
            )
        )
        codes = await conn.scalar(text("SELECT count(*) FROM user_recovery_codes"))
        resets = await conn.scalar(
            text("SELECT count(*) FROM audit_events WHERE action = 'auth.two_factor_reset'")
        )
    assert (left, codes, resets) == (0, 0, 2)
    me = (await login(client, "anna@example.com")).json()
    assert me["two_factor_enabled"] is False


async def test_every_workspace_gets_a_new_key_of_its_own(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, master_key
):
    leaked, new = generate_master_key(), generate_master_key()
    master_key(leaked)
    accounts = {}
    for email in ("anna@example.com", "boris@example.com"):
        tenant_id = await make_user(email)
        client.cookies.clear()
        await login(client, email)
        accounts[email] = (tenant_id, await _account(client, await contact(client), email))
    before = {email: await _stored_key(owner_engine, t) for email, (t, _) in accounts.items()}

    master_key(new, previous=leaked)
    await rekey_after_leak()
    master_key(new)

    keys = set()
    for email, (tenant_id, account) in accounts.items():
        assert tenant_id is not None
        old_key = decrypt(_raw(leaked), before[email], tenant_key_aad(tenant_id))
        new_key = decrypt(
            _raw(new), await _stored_key(owner_engine, tenant_id), tenant_key_aad(tenant_id)
        )
        assert new_key != old_key
        keys.add(new_key)
        client.cookies.clear()
        await login(client, email)
        assert (await reveal(client, account["id"])).json() == {"password": email}
    assert len(keys) == 2


async def test_a_stopped_rekey_goes_on_from_where_it_stopped(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    master_key,
    monkeypatch: pytest.MonkeyPatch,
):
    # Files are encrypted again in place, one by one: a storage that fails halfway leaves
    # some under the new key while the database still has the old one. The next run
    # comes to the same new key and goes on, so no file is lost.
    leaked, new = generate_master_key(), generate_master_key()
    master_key(leaked)
    tenant_id = await make_user("anna@example.com")
    assert tenant_id is not None
    await turn_on(owner_engine, tenant_id)
    await login(client, "anna@example.com")
    anna = await contact(client)
    documents = [(await upload_document(client, anna, name=f"{i}.pdf")).json() for i in range(3)]
    template = (await upload_template(client)).json()
    account = await _account(client, anna, "Kept-3")

    master_key(new, previous=leaked)
    put = storage.put_object
    sent = 0

    async def failing_put(key: str, data: bytes) -> None:
        nonlocal sent
        sent += 1
        if sent == 3:
            raise storage.StorageFailed("ReadTimeoutError")
        await put(key, data)

    monkeypatch.setattr(storage, "put_object", failing_put)
    with pytest.raises(storage.StorageFailed):
        await rekey_after_leak()
    monkeypatch.setattr(storage, "put_object", put)
    await rekey_after_leak()
    master_key(new)

    client.cookies.clear()
    await login(client, "anna@example.com")
    for document in documents:
        download = await client.get(f"/api/contacts/{anna}/documents/{document['id']}")
        assert download.status_code == 200 and download.content == PDF
    blank = await client.get(f"/api/templates/{template['id']}/file")
    assert blank.status_code == 200 and blank.content == LETTER
    assert (await reveal(client, account["id"])).json() == {"password": "Kept-3"}
    # Nothing but the files is left in the storage.
    assert set(storage.MEMORY) == {
        *(document_key(tenant_id, uuid.UUID(d["id"])) for d in documents),
        template_key(tenant_id, uuid.UUID(template["id"])),
    }
    async with owner_engine.connect() as conn:
        current = await conn.scalar(
            text("SELECT vault_key_encrypted FROM tenants WHERE id = :id"), {"id": tenant_id}
        )
    key = decrypt(_raw(new), current, tenant_key_aad(tenant_id))
    blob = storage.MEMORY[template_key(tenant_id, uuid.UUID(template["id"]))]
    assert open_template(key, tenant_id, uuid.UUID(template["id"]), blob) == LETTER


async def test_a_rekey_waits_for_a_request_holding_the_old_key(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    # A password change takes the key first and writes the row later. Had the rekey not
    # waited, the change would land encrypted with a key that no longer exists.
    tenant_id = await make_user("anna@example.com")
    assert tenant_id is not None
    await login(client, "anna@example.com")
    account = await _account(client, await contact(client), "Old-1")
    entry_id = uuid.UUID(account["id"])

    async with sessionmaker() as request, sessionmaker() as rekey:
        scope_to_tenant(request, tenant_id)
        scope_to_system(rekey)
        old_key = await get_tenant_key(request, tenant_id)
        running = asyncio.create_task(rekey_workspace(rekey, tenant_id))
        await _until_one_waits_for_a_lock(owner_engine)
        await request.execute(
            update(VaultEntry)
            .where(VaultEntry.id == entry_id)
            .values(password_encrypted=encrypt_for_tenant(old_key, tenant_id, entry_id, "New-2"))
        )
        await request.commit()
        await running
        await rekey.commit()

    assert (await reveal(client, account["id"])).json() == {"password": "New-2"}

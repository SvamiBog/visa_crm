"""Erasing a workspace's data (the specialist moves to another system): only the owner,
only with two-factor login on, confirmed with the password and a code from the app.
Every row of the workspace and its files go; the account, its settings, the subscription
and the audit log stay."""

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core import import_jobs, storage
from app.core.db import Base, scope_to_system
from app.core.demo import DemoSettings, seed, specialist_email
from app.core.erase import KEPT_TABLES
from tests.support.helpers import CLOCK, PASSWORD, TOTP_SECRET, MakeUser, enable_two_factor, login
from tests.test_documents import upload as upload_document
from tests.test_templates import upload as upload_template

DEMO_PASSWORD = "demo-password-42"
ERASE = "/api/workspace/erase"

# Tables of a workspace's data: every one with a tenant_id.
TENANT_TABLES = [t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c]


async def _counts(engine: AsyncEngine, tenant_id: uuid.UUID) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            table: await conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
            )
            for table in TENANT_TABLES
        }


async def _tenant_of(engine: AsyncEngine, email: str) -> uuid.UUID:
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT tenant_id FROM users WHERE email = :e"), {"e": email})


async def _seed_two_workspaces(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_system(session)
        await seed(session, DemoSettings(DEMO_PASSWORD, TOTP_SECRET, specialists=2, clients=12))
        await session.commit()


async def test_erasing_removes_every_workspace_row_and_file_but_keeps_the_account(
    client: AsyncClient,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    await _seed_two_workspaces(sessionmaker)
    mine = await _tenant_of(owner_engine, specialist_email(1))
    other = await _tenant_of(owner_engine, specialist_email(2))
    assert (await login(client, specialist_email(1), DEMO_PASSWORD)).status_code == 200

    # A file of a client and a document template: both live in the file storage.
    anna = (await client.post("/api/contacts/search", json={})).json()["items"][0]["id"]
    assert (await upload_document(client, anna)).status_code == 201
    assert (await upload_template(client, name="Письмо")).status_code == 201
    assert any(key.startswith(f"documents/{mine}/") for key in storage.MEMORY)
    assert any(key.startswith(f"templates/{mine}/") for key in storage.MEMORY)

    before = await _counts(owner_engine, mine)
    assert before["contacts"] > 0 and before["visa_cases"] > 0 and before["vault_entries"] > 0
    assert before["users"] == 2  # the specialist and the demo employee
    others_before = await _counts(owner_engine, other)
    others_files = {k for k in storage.MEMORY if str(other) in k}

    erased = await client.post(ERASE, json={"password": DEMO_PASSWORD, "code": CLOCK.next_code()})
    assert erased.status_code == 204, erased.text

    after = await _counts(owner_engine, mine)
    # Every table not kept on purpose is empty, apart from the default lists a new
    # workspace starts with.
    defaults = {"visa_purposes", "lead_channels", "vault_systems"}
    for table in TENANT_TABLES:
        if table in KEPT_TABLES or table in defaults:
            continue
        assert after[table] == 0, table
    for table in defaults:
        assert after[table] > 0, table
    assert after["users"] == 1  # the employee is gone, the specialist stays
    assert after["subscription_payments"] == before["subscription_payments"] > 0
    assert after["audit_events"] > before["audit_events"]
    assert not any(str(mine) in key for key in storage.MEMORY)

    # The other workspace is untouched.
    assert await _counts(owner_engine, other) == others_before
    assert {k for k in storage.MEMORY if str(other) in k} == others_files

    # The specialist stays signed in to an empty workspace with the old settings.
    assert (await client.get("/api/workspace")).status_code == 200
    assert (await client.post("/api/contacts/search", json={})).json()["items"] == []
    events = (await client.get("/api/audit")).json()["events"]
    assert events[0]["action"] == "workspace.erased"
    async with owner_engine.connect() as conn:
        tenant = (
            await conn.execute(
                text("SELECT name, modules, vault_key_encrypted FROM tenants WHERE id = :t"),
                {"t": mine},
            )
        ).one()
    assert tenant.name.endswith("(демо 1)") and tenant.modules
    # The vault key is dropped with the secrets: copies of them left anywhere (backups,
    # files not removed from the storage) no longer open with this database.
    assert tenant.vault_key_encrypted is None
    # A new password in the vault gets a new key and opens.
    systems = (await client.get("/api/dictionaries/vault-systems")).json()
    entry = await client.post(
        "/api/vault/entries",
        json={"system_id": systems[0]["id"], "login": "me", "password": "Secret-123"},
    )
    assert entry.status_code == 201, entry.text
    shown = await client.post(f"/api/vault/entries/{entry.json()['id']}/reveal")
    assert shown.json()["password"] == "Secret-123"

    # The employee can no longer sign in.
    client.cookies.clear()
    staff = await login(client, "visa1.staff@demo.visacrm.test", DEMO_PASSWORD)
    assert staff.status_code == 401


async def test_erasing_needs_two_factor_login_on(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    await client.post("/api/contacts", json={"last_name": "Ковалёва", "first_name": "Анна"})

    refused = await client.post(ERASE, json={"password": PASSWORD, "code": "123456"})
    assert refused.status_code == 409
    assert refused.json() == {"detail": "two_factor_required"}
    assert (await client.post("/api/contacts/search", json={})).json()["items"]

    # Once it is on, the same form works.
    secret, _ = await enable_two_factor(client)
    erased = await client.post(ERASE, json={"password": PASSWORD, "code": CLOCK.next_code(secret)})
    assert erased.status_code == 204
    assert (await client.post("/api/contacts/search", json={})).json()["items"] == []


async def test_erasing_asks_for_the_password_and_a_code_from_the_app(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    secret, recovery_codes = await enable_two_factor(client)
    await client.post("/api/contacts", json={"last_name": "Ковалёва", "first_name": "Анна"})

    wrong_password = await client.post(
        ERASE, json={"password": "not-my-password", "code": CLOCK.next_code(secret)}
    )
    assert wrong_password.status_code == 400
    assert wrong_password.json() == {"detail": "invalid_current_password"}
    wrong_code = await client.post(ERASE, json={"password": PASSWORD, "code": "000000"})
    assert wrong_code.json() == {"detail": "invalid_code"}
    # A recovery code signs in when the phone is lost, but does not erase the data.
    recovery = await client.post(ERASE, json={"password": PASSWORD, "code": recovery_codes[0]})
    assert recovery.json() == {"detail": "invalid_code"}

    assert (await client.post("/api/contacts/search", json={})).json()["items"]
    # The misses count with wrong logins, like the other forms of the settings.
    async with owner_engine.connect() as conn:
        failures = await conn.scalar(
            text("SELECT failed_login_count FROM users WHERE email = 'anna@example.com'")
        )
    assert failures >= 3


async def test_a_code_from_the_app_erases_only_once(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    code = CLOCK.next_code()
    assert (await client.post(ERASE, json={"password": PASSWORD, "code": code})).status_code == 204
    replay = await client.post(ERASE, json={"password": PASSWORD, "code": code})
    assert replay.json() == {"detail": "invalid_code"}


async def test_only_the_specialist_erases(client: AsyncClient, make_user: MakeUser):
    tenant_id = await make_user("anna@example.com")
    await make_user("olga@example.com", "employee", tenant_id=tenant_id, two_factor=True)
    await login(client, "olga@example.com")
    refused = await client.post(ERASE, json={"password": PASSWORD, "code": CLOCK.next_code()})
    assert refused.status_code == 403

    client.cookies.clear()
    await make_user("admin@example.com", "admin")
    await login(client, "admin@example.com")
    refused = await client.post(ERASE, json={"password": PASSWORD, "code": CLOCK.next_code()})
    assert refused.status_code == 403


async def test_erasing_waits_for_a_running_import(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, monkeypatch
):
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await client.post("/api/contacts", json={"last_name": "Ковалёва", "first_name": "Анна"})

    def an_import(tenant: uuid.UUID) -> list[import_jobs.ImportJob]:
        assert tenant == tenant_id
        return [import_jobs.ImportJob(uuid.uuid4(), tenant, "import", "clients.xlsx")]

    monkeypatch.setattr(import_jobs, "running", an_import)
    busy = await client.post(ERASE, json={"password": PASSWORD, "code": CLOCK.next_code()})
    assert busy.status_code == 409
    assert busy.json() == {"detail": "import_running"}
    assert (await client.post("/api/contacts/search", json={})).json()["items"]
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM users")) == 1

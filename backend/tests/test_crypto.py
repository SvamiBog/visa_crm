import os
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.crypto import (
    VaultKeyError,
    decrypt,
    encrypt,
    generate_master_key,
    is_sealed_with_current_key,
    seal,
    unseal,
)
from app.core.db import scope_to_tenant
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key

KEY = bytes(32)
OTHER_KEY = bytes([1]) * 32


def test_round_trip_and_random_nonce():
    first = encrypt(KEY, b"secret", b"owner-1")
    assert decrypt(KEY, first, b"owner-1") == b"secret"
    assert encrypt(KEY, b"secret", b"owner-1") != first
    assert b"secret" not in first


@pytest.mark.parametrize(
    ("key", "aad", "tamper"),
    [(OTHER_KEY, b"owner-1", False), (KEY, b"owner-2", False), (KEY, b"owner-1", True)],
    ids=["wrong key", "blob moved to another owner", "tampered blob"],
)
def test_decrypt_rejects(key: bytes, aad: bytes, tamper: bool):
    blob = bytearray(encrypt(KEY, b"secret", b"owner-1"))
    if tamper:
        blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(key, bytes(blob), aad)


@pytest.mark.parametrize(
    "value", ["", "not base64!", generate_master_key()[:20]], ids=["missing", "bad", "short"]
)
def test_master_key_must_be_valid(master_key, value: str):
    master_key(value)
    with pytest.raises(VaultKeyError):
        seal(b"x", b"aad")


def test_rotation_keeps_old_secrets_readable(master_key):
    old, new = generate_master_key(), generate_master_key()
    master_key(old)
    blob = seal(b"secret", b"aad")

    master_key(new, previous=old)
    assert unseal(blob, b"aad") == b"secret"
    assert not is_sealed_with_current_key(blob, b"aad")
    resealed = seal(unseal(blob, b"aad"), b"aad")

    master_key(new)
    assert unseal(resealed, b"aad") == b"secret"
    with pytest.raises(VaultKeyError):
        unseal(blob, b"aad")


async def test_tenant_key_is_created_once_and_stored_wrapped(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession]
):
    tenant_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'A')"), {"id": tenant_id}
        )

    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        key = await get_tenant_key(session, tenant_id)
        await session.commit()
        assert await get_tenant_key(session, tenant_id) == key

    async with owner_engine.connect() as conn:
        stored = await conn.scalar(
            text("SELECT vault_key_encrypted FROM tenants WHERE id = :id"), {"id": tenant_id}
        )
    assert stored is not None and key not in stored

    blob = encrypt_for_tenant(key, tenant_id, "client-password")
    assert decrypt_for_tenant(key, tenant_id, blob) == "client-password"
    with pytest.raises(InvalidTag):
        decrypt_for_tenant(key, uuid.uuid4(), blob)


async def test_tenant_cannot_load_another_tenants_key(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession]
):
    a, b = uuid.uuid4(), uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:a, 'A'), (:b, 'B')"), {"a": a, "b": b}
        )
    async with sessionmaker() as session:
        scope_to_tenant(session, b)
        with pytest.raises(LookupError):
            await get_tenant_key(session, a)


def test_generated_master_key_is_32_bytes():
    import base64

    assert len(base64.b64decode(generate_master_key())) == 32
    assert os.environ["VAULT_MASTER_KEY"]  # tests run with a master key set by conftest


async def test_rotation_reseals_tenant_keys(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession], master_key
):
    from app.core.db import scope_to_system
    from app.core.vault import reseal_with_current_master_key

    old, new = generate_master_key(), generate_master_key()
    master_key(old)
    tenant_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'A')"), {"id": tenant_id}
        )
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        key = await get_tenant_key(session, tenant_id)
        await session.commit()

    master_key(new, previous=old)
    async with sessionmaker() as session:
        scope_to_system(session)
        assert await reseal_with_current_master_key(session) == 1
        await session.commit()
    async with sessionmaker() as session:
        scope_to_system(session)
        assert await reseal_with_current_master_key(session) == 0

    master_key(new)  # old key removed: the tenant key still opens
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        assert await get_tenant_key(session, tenant_id) == key

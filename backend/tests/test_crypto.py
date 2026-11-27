"""The workspace's vault key in the database (app/core/vault.py): created once, stored
wrapped by the master key, closed to another workspace and resealed when the master key
is rotated. Encryption itself, without a database, is tests/test_crypto_rules.py."""

import uuid

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.crypto import generate_master_key
from app.core.db import scope_to_tenant
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key


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

    entry_id = uuid.uuid4()
    blob = encrypt_for_tenant(key, tenant_id, entry_id, "client-password")
    assert decrypt_for_tenant(key, tenant_id, entry_id, blob) == "client-password"
    with pytest.raises(InvalidTag):
        decrypt_for_tenant(key, uuid.uuid4(), entry_id, blob)
    with pytest.raises(InvalidTag):  # copied onto another entry
        decrypt_for_tenant(key, tenant_id, uuid.uuid4(), blob)


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

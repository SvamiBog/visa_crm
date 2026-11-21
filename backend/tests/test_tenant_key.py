"""The workspace's vault key under concurrent use (backlog S6, audit M6, h03.1, h03.5) and
the database limits on the app's connections (h19.2).

A key in use must not hold the workspace row: an import keeps its transaction open for the
whole run, and every insert of the workspace checks its foreign key on that row."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core import db
from app.core.crypto import generate_master_key, unseal
from app.core.db import scope_to_system, scope_to_tenant
from app.core.two_factor import seal_secret
from app.core.vault import (
    decrypt_for_tenant,
    encrypt_for_tenant,
    get_tenant_key,
    reseal_with_current_master_key,
    tenant_key_aad,
)
from app.models import ContactNote, Tenant
from tests.helpers import TOTP_SECRET

# How long a session that must not wait for the workspace row may wait for a lock.
NO_WAIT = "1s"


async def _waits_at_most_a_second(session: AsyncSession) -> None:
    """Begin the session's transaction with a short lock_timeout: waiting for the row
    fails it instead of passing unnoticed."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{NO_WAIT}'"))


async def _until_one_waits_for_a_lock(owner_engine: AsyncEngine) -> None:
    for _ in range(100):
        async with owner_engine.connect() as conn:
            waiting = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no session waited for the workspace row")


Workspace = tuple[uuid.UUID, uuid.UUID, uuid.UUID]


async def _workspace(owner_engine: AsyncEngine) -> Workspace:
    """A workspace without a key yet, its specialist (two-factor login on) and a client."""
    tenant_id, user_id, contact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'A')"), {"id": tenant_id}
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, totp_secret_encrypted) "
                "VALUES (:id, :t, 'a@example.com', 'specialist', :secret)"
            ),
            {"id": user_id, "t": tenant_id, "secret": seal_secret(user_id, TOTP_SECRET)},
        )
        await conn.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, last_name, full_name) "
                "VALUES (:id, :t, 'Client', 'Client')"
            ),
            {"id": contact_id, "t": tenant_id},
        )
    return tenant_id, user_id, contact_id


@pytest.fixture
async def workspace(owner_engine: AsyncEngine) -> Workspace:
    return await _workspace(owner_engine)


def _note(workspace: Workspace) -> ContactNote:
    tenant_id, user_id, contact_id = workspace
    return ContactNote(tenant_id=tenant_id, contact_id=contact_id, author_id=user_id, body="Note")


async def _key(sessionmaker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID) -> bytes:
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        key = await get_tenant_key(session, tenant_id)
        await session.commit()
    return key


async def test_a_key_in_use_lets_the_workspace_write_and_read_it(
    sessionmaker: async_sessionmaker[AsyncSession], workspace
):
    tenant_id = workspace[0]
    key = await _key(sessionmaker, tenant_id)

    async with sessionmaker() as holder, sessionmaker() as other:
        scope_to_tenant(holder, tenant_id)
        scope_to_tenant(other, tenant_id)
        # As an import that took the key at its first password: its transaction stays open.
        assert await get_tenant_key(holder, tenant_id) == key
        await _waits_at_most_a_second(other)
        other.add(_note(workspace))
        await other.flush()
        assert await get_tenant_key(other, tenant_id) == key
        await other.commit()
        await holder.commit()


async def test_making_the_key_lets_the_workspace_write(
    sessionmaker: async_sessionmaker[AsyncSession], workspace
):
    tenant_id = workspace[0]
    async with sessionmaker() as maker, sessionmaker() as other:
        scope_to_tenant(maker, tenant_id)
        scope_to_tenant(other, tenant_id)
        key = await get_tenant_key(maker, tenant_id)  # first use: made, not committed yet
        await _waits_at_most_a_second(other)
        other.add(_note(workspace))
        await other.commit()
        await maker.commit()
    assert await _key(sessionmaker, tenant_id) == key


async def test_a_session_holding_the_workspace_keeps_a_key_made_meanwhile(
    sessionmaker: async_sessionmaker[AsyncSession], workspace
):
    tenant_id = workspace[0]
    entry_id = uuid.uuid4()
    async with sessionmaker() as holder:
        scope_to_tenant(holder, tenant_id)
        # As an import: the workspace is read at the start, before it has a key.
        tenant = await holder.get(Tenant, tenant_id)
        assert tenant is not None and tenant.vault_key_encrypted is None

        key = await _key(sessionmaker, tenant_id)
        secret = encrypt_for_tenant(key, tenant_id, entry_id, "client-password")

        assert await get_tenant_key(holder, tenant_id) == key
        await holder.commit()

    later = await _key(sessionmaker, tenant_id)
    assert decrypt_for_tenant(later, tenant_id, entry_id, secret) == "client-password"


async def test_first_uses_at_once_make_one_key(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession], workspace
):
    tenant_id = workspace[0]
    async with sessionmaker() as first, sessionmaker() as second:
        scope_to_tenant(first, tenant_id)
        scope_to_tenant(second, tenant_id)
        # The second holds the workspace without a key, as an import does from its start
        # (the identity map keeps only what is referenced).
        held = await second.get(Tenant, tenant_id)
        assert held is not None and held.vault_key_encrypted is None
        key = await get_tenant_key(first, tenant_id)
        waiting = asyncio.create_task(get_tenant_key(second, tenant_id))
        await _until_one_waits_for_a_lock(owner_engine)
        await first.commit()
        assert await waiting == key
        await second.commit()

    async with owner_engine.connect() as conn:
        stored = await conn.scalar(
            text("SELECT vault_key_encrypted FROM tenants WHERE id = :id"), {"id": tenant_id}
        )
    assert unseal(stored, tenant_key_aad(tenant_id)) == key


async def test_requests_at_once_on_a_new_workspace_get_one_key(
    sessionmaker: async_sessionmaker[AsyncSession], workspace
):
    tenant_id = workspace[0]
    keys = await asyncio.gather(*(_key(sessionmaker, tenant_id) for _ in range(5)))
    assert len(set(keys)) == 1


async def test_rotation_does_not_wait_for_the_workspace_writes(
    sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine, master_key
):
    old, new = generate_master_key(), generate_master_key()
    master_key(old)
    # The vault key and the specialist's two-factor secret, both sealed with the old one.
    workspace = await _workspace(owner_engine)
    tenant_id = workspace[0]
    await _key(sessionmaker, tenant_id)

    master_key(new, previous=old)
    async with sessionmaker() as writer, sessionmaker() as rotation:
        scope_to_tenant(writer, tenant_id)
        writer.add(_note(workspace))  # holds the workspace and the author's rows (FK checks)
        await writer.flush()
        scope_to_system(rotation)
        await _waits_at_most_a_second(rotation)
        assert await reseal_with_current_master_key(rotation) == 2
        await rotation.commit()
        await writer.commit()


async def test_the_app_engine_bounds_lock_waits_and_idle_transactions(migrated_database: str):
    db.get_sessionmaker.cache_clear()
    db.get_engine.cache_clear()
    try:
        async with db.get_sessionmaker()() as session:
            scope_to_system(session)  # the app's role in the transaction keeps the limits
            lock = await session.scalar(text("SHOW lock_timeout"))
            idle = await session.scalar(text("SHOW idle_in_transaction_session_timeout"))
    finally:
        await db.get_engine().dispose()
        db.get_sessionmaker.cache_clear()
        db.get_engine.cache_clear()
    assert (lock, idle) == (db.LOCK_TIMEOUT, db.IDLE_IN_TRANSACTION_TIMEOUT)

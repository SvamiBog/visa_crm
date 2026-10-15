import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import MAX_FAILED_LOGINS
from app.cli import create_admin
from app.core.db import scope_to_tenant
from tests.helpers import MakeUser, enable_two_factor, login


async def actions(client: AsyncClient, path: str = "/api/audit") -> list[str]:
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return [event["action"] for event in response.json()["events"]]


async def test_logins_are_recorded(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com", "wrong-password")
    await login(client, "anna@example.com")
    events = (await client.get("/api/audit")).json()["events"]
    assert [e["action"] for e in events] == ["auth.login", "auth.login_failed"]
    assert events[0]["actor_email"] == "anna@example.com"
    assert events[0]["details"] == {"method": "totp"}
    assert events[1]["details"] == {"reason": "password"}
    assert events[0]["ip"] == "127.0.0.1"


async def test_unknown_email_leaves_no_trace(client: AsyncClient, owner_engine: AsyncEngine):
    await login(client, "ghost@example.com")
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM audit_events")) == 0


async def test_lockout_is_recorded(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        await login(client, "anna@example.com", "wrong-password")
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    logged = await actions(client, "/api/admin/audit")
    assert logged.count("auth.login_failed") == MAX_FAILED_LOGINS
    assert "auth.account_locked" in logged


async def test_account_events_are_recorded(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com", must_change_password=True)
    await login(client, "anna@example.com")
    await client.post(
        "/api/auth/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "new-password-123"},
    )
    await enable_two_factor(client)
    assert await actions(client) == [
        "auth.two_factor_enabled",
        "auth.password_changed",
        "auth.login",
    ]
    await client.post("/api/auth/logout")
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    assert "auth.logout" in await actions(client, "/api/admin/audit")


async def test_specialist_sees_admin_actions_on_their_workspace(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("root@example.com", role="admin")
    tenant_id = await make_user("anna@example.com")
    await login(client, "root@example.com")
    await client.post(f"/api/admin/specialists/{tenant_id}/block")
    await client.post(f"/api/admin/specialists/{tenant_id}/unblock")
    await client.post("/api/auth/logout")

    await login(client, "anna@example.com")
    events = (await client.get("/api/audit")).json()["events"]
    admin_events = [e for e in events if e["action"].startswith("admin.")]
    assert [e["action"] for e in admin_events] == [
        "admin.specialist_unblocked",
        "admin.specialist_blocked",
    ]
    assert {e["actor_email"] for e in admin_events} == {"root@example.com"}
    # The admin's own login is platform-level: not in the specialist's log.
    assert all(
        e["actor_email"] != "root@example.com" for e in events if e["action"] == "auth.login"
    )


async def test_specialist_sees_only_own_workspace(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await make_user("boris@example.com")
    await login(client, "anna@example.com")
    await client.post("/api/auth/logout")
    await login(client, "boris@example.com")
    events = (await client.get("/api/audit")).json()["events"]
    assert {e["actor_email"] for e in events} == {"boris@example.com"}


async def test_admin_can_filter_by_workspace(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    anna = await make_user("anna@example.com")
    await make_user("boris@example.com")
    for email in ("anna@example.com", "boris@example.com", "root@example.com"):
        await login(client, email)
    events = (await client.get(f"/api/admin/audit?tenant_id={anna}")).json()["events"]
    assert {e["actor_email"] for e in events} == {"anna@example.com"}


async def test_employee_cannot_read_the_log(client: AsyncClient, make_user: MakeUser):
    tenant_id = await make_user("anna@example.com")
    await make_user("staff@example.com", role="employee", tenant_id=tenant_id)
    await login(client, "staff@example.com")
    assert (await client.get("/api/audit")).status_code == 403
    assert (await client.get("/api/admin/audit")).status_code == 403


async def test_pages_newest_first(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    for _ in range(3):
        await login(client, "anna@example.com", "wrong-password")
    await login(client, "anna@example.com")
    first = (await client.get("/api/audit?limit=2")).json()
    assert len(first["events"]) == 2 and first["next_before"] is not None
    second = (await client.get(f"/api/audit?limit=2&before={first['next_before']}")).json()
    assert len(second["events"]) == 2 and second["next_before"] is None
    ids = [e["id"] for e in first["events"] + second["events"]]
    assert ids == sorted(ids, reverse=True)


async def test_cli_admin_creation_is_recorded(client: AsyncClient, owner_engine: AsyncEngine):
    await create_admin("root@example.com", "admin-password-123")
    async with owner_engine.connect() as conn:
        action = await conn.scalar(text("SELECT action FROM audit_events"))
    assert action == "admin.admin_created"


@pytest.fixture
async def event_in_tenant(owner_engine: AsyncEngine) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'A')"), {"id": tenant_id}
        )
        await conn.execute(
            text("INSERT INTO audit_events (tenant_id, action) VALUES (:id, 'auth.login')"),
            {"id": tenant_id},
        )
    return tenant_id


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"],
)
async def test_app_cannot_change_history(
    sessionmaker: async_sessionmaker[AsyncSession], event_in_tenant: uuid.UUID, statement: str
):
    async with sessionmaker() as session:
        scope_to_tenant(session, event_in_tenant)
        with pytest.raises(DBAPIError, match="permission denied"):
            await session.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"],
)
async def test_even_the_owner_cannot_change_history(
    owner_engine: AsyncEngine, event_in_tenant: uuid.UUID, statement: str
):
    async with owner_engine.begin() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            await conn.execute(text(statement))

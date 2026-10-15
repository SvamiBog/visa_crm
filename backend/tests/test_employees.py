import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from tests.helpers import NEW_PASSWORD, MakeUser, enable_two_factor, login


async def invite(client: AsyncClient, email: str = "staff@example.com", **extra) -> dict:
    body = {"full_name": "Olga Staff", "email": email, "modules": ["clients"], **extra}
    response = await client.post("/api/employees", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_specialist_invites_employee_who_works_with_granted_modules(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")  # core modules on
    await login(client, "anna@example.com")
    created = await invite(client, "Staff@Example.com", modules=["clients", "analytics"])
    assert created["employee"]["email"] == "staff@example.com"
    assert created["employee"]["must_change_password"] is True
    listed = (await client.get("/api/employees")).json()
    assert [e["full_name"] for e in listed] == ["Olga Staff"]
    await client.post("/api/auth/logout")

    me = (await login(client, "staff@example.com", created["temporary_password"])).json()
    assert (me["role"], me["must_change_password"]) == ("employee", True)
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": created["temporary_password"], "new_password": NEW_PASSWORD},
    )
    assert changed.json()["must_set_up_two_factor"] is False  # optional for employees
    workspace = (await client.get("/api/workspace")).json()
    assert workspace["modules"] == ["clients", "analytics"]


@pytest.mark.parametrize(
    ("modules", "status", "detail"),
    [(["slots"], 400, "module_not_available"), (["teleport"], 422, None)],
)
async def test_only_the_specialists_modules_can_be_granted(
    client: AsyncClient, make_user: MakeUser, modules: list[str], status: int, detail: str | None
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.post(
        "/api/employees",
        json={"full_name": "Olga", "email": "staff@example.com", "modules": modules},
    )
    assert response.status_code == status
    if detail:
        assert response.json() == {"detail": detail}


async def test_email_taken_in_another_workspace(client: AsyncClient, make_user: MakeUser):
    await make_user("boris@example.com")
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.post(
        "/api/employees", json={"full_name": "X", "email": "boris@example.com", "modules": []}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "email_taken"}


async def test_changing_modules_is_logged(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    employee = (await invite(client))["employee"]
    updated = await client.patch(
        f"/api/employees/{employee['id']}", json={"modules": ["cases", "clients"]}
    )
    assert updated.json()["modules"] == ["clients", "cases"]
    event = (await client.get("/api/audit")).json()["events"][0]
    assert event["action"] == "employees.updated"
    assert event["details"] == {
        "email": "staff@example.com",
        "changed": ["modules"],
        "modules_on": ["cases"],
        "modules_off": [],
    }


async def test_blocking_signs_the_employee_out(client: AsyncClient, make_user: MakeUser):
    tenant_id = await make_user("anna@example.com")
    await make_user("staff@example.com", role="employee", tenant_id=tenant_id)
    await login(client, "staff@example.com")
    staff_cookies = dict(client.cookies)

    client.cookies.clear()
    await login(client, "anna@example.com")
    staff_id = (await client.get("/api/employees")).json()[0]["id"]
    blocked = await client.post(f"/api/employees/{staff_id}/block")
    assert blocked.json()["is_active"] is False
    specialist_cookies = dict(client.cookies)

    client.cookies = staff_cookies
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await login(client, "staff@example.com")).json() == {"detail": "account_disabled"}

    client.cookies = specialist_cookies
    await client.post(f"/api/employees/{staff_id}/unblock")
    client.cookies.clear()
    assert (await login(client, "staff@example.com")).status_code == 200


async def test_new_password_resets_two_factor_and_sessions(
    client: AsyncClient, make_user: MakeUser
):
    tenant_id = await make_user("anna@example.com")
    await make_user("staff@example.com", role="employee", tenant_id=tenant_id)
    await login(client, "staff@example.com")
    await enable_two_factor(client)
    staff_cookies = dict(client.cookies)

    client.cookies.clear()
    await login(client, "anna@example.com")
    staff_id = (await client.get("/api/employees")).json()[0]["id"]
    temporary = (await client.post(f"/api/employees/{staff_id}/reset-password")).json()[
        "temporary_password"
    ]

    client.cookies = staff_cookies
    assert (await client.get("/api/auth/me")).status_code == 401
    client.cookies.clear()
    me = (await login(client, "staff@example.com", temporary)).json()
    assert me["must_change_password"] is True and me["two_factor_enabled"] is False


async def test_specialist_cannot_touch_another_workspace(client: AsyncClient, make_user: MakeUser):
    anna = await make_user("anna@example.com")
    await make_user("staff@example.com", role="employee", tenant_id=anna)
    await make_user("boris@example.com")
    await login(client, "anna@example.com")
    staff_id = (await client.get("/api/employees")).json()[0]["id"]
    await client.post("/api/auth/logout")

    await login(client, "boris@example.com")
    assert (await client.get("/api/employees")).json() == []
    for action in ("block", "unblock", "reset-password"):
        response = await client.post(f"/api/employees/{staff_id}/{action}")
        assert response.status_code == 404
    patched = await client.patch(f"/api/employees/{staff_id}", json={"full_name": "Hacked"})
    assert patched.status_code == 404


async def test_specialist_cannot_manage_themselves_as_employee(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    me = (await login(client, "anna@example.com")).json()
    assert (await client.post(f"/api/employees/{me['id']}/block")).status_code == 404


@pytest.mark.parametrize("role", ["employee", "admin"])
async def test_only_specialists_manage_employees(
    client: AsyncClient, make_user: MakeUser, role: str
):
    tenant_id = await make_user("anna@example.com") if role == "employee" else None
    await make_user("someone@example.com", role=role, tenant_id=tenant_id)
    await login(client, "someone@example.com")
    assert (await client.get("/api/employees")).status_code == 403
    created = await client.post(
        "/api/employees", json={"full_name": "X", "email": "x@example.com", "modules": []}
    )
    assert created.status_code == 403


async def test_workspace_scope_sees_only_own_users_sessions(
    client: AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    anna = await make_user("anna@example.com")
    await make_user("boris@example.com")
    await login(client, "anna@example.com")
    client.cookies.clear()
    await login(client, "boris@example.com")
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert await session.scalar(text("SELECT count(*) FROM user_sessions")) == 1
        deleted = await session.execute(text("DELETE FROM user_sessions"))
        assert deleted.rowcount == 1  # only Anna's own session

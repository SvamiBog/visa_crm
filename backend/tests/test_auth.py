import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.auth import MAX_FAILED_LOGINS
from app.api.deps import SESSION_COOKIE
from tests.helpers import NEW_PASSWORD, PASSWORD, MakeUser, enable_two_factor, login


async def test_login_sets_http_only_session_cookie(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    response = await login(client, "Anna@Example.com ")
    assert response.status_code == 200
    assert response.json()["role"] == "specialist"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie

    me = await client.get("/api/auth/me")
    assert me.json()["email"] == "anna@example.com"


async def test_session_token_is_stored_hashed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    token = client.cookies[SESSION_COOKIE]
    async with owner_engine.connect() as conn:
        stored = (await conn.execute(text("SELECT token_hash FROM user_sessions"))).scalars().all()
    assert len(stored) == 1 and token not in stored


async def test_wrong_password_and_unknown_email_look_the_same(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    wrong = await login(client, "anna@example.com", "nope")
    unknown = await login(client, "ghost@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid_credentials"}
    assert SESSION_COOKIE not in client.cookies


async def test_lockout_after_ten_failed_attempts(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        assert (await login(client, "anna@example.com", "nope")).status_code == 401
    locked = await login(client, "anna@example.com")
    assert locked.status_code == 423
    assert locked.json() == {"detail": "account_locked"}


async def test_successful_login_resets_failed_attempts(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    for _ in range(MAX_FAILED_LOGINS - 1):
        await login(client, "anna@example.com", "nope")
    assert (await login(client, "anna@example.com")).status_code == 200
    assert (await login(client, "anna@example.com", "nope")).status_code == 401
    assert (await login(client, "anna@example.com")).status_code == 200


async def test_logout_ends_session(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    token = client.cookies[SESSION_COOKIE]
    assert (await client.post("/api/auth/logout")).status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # a stolen copy of the cookie stops working too
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_temporary_password_allows_only_password_change(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com", must_change_password=True)
    me = (await login(client, "anna@example.com")).json()
    assert me["must_change_password"] is True
    blocked = await client.get("/api/workspace")
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "password_change_required"}

    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    # Two-factor login is optional: the app opens right away.
    assert (await client.get("/api/workspace")).status_code == 200
    secret, _ = await enable_two_factor(client)

    await client.post("/api/auth/logout")
    assert (await login(client, "anna@example.com")).status_code == 401
    relogin = await login(client, "anna@example.com", NEW_PASSWORD, secret=secret)
    assert relogin.status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "status", "detail"),
    [
        ("wrong-password", NEW_PASSWORD, 400, "invalid_current_password"),
        (PASSWORD, PASSWORD, 400, "password_unchanged"),
        (PASSWORD, "short", 422, None),
    ],
)
async def test_change_password_rules(
    client: AsyncClient, make_user: MakeUser, current: str, new: str, status: int, detail: str
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.post(
        "/api/auth/change-password", json={"current_password": current, "new_password": new}
    )
    assert response.status_code == status
    if detail:
        assert response.json() == {"detail": detail}


async def test_password_change_signs_out_other_devices(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    other_device = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    await login(client, "anna@example.com")
    await client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert (await client.get("/api/auth/me")).status_code == 200
    client.cookies.set(SESSION_COOKIE, other_device)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_workspace_shows_only_own_tenant(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    assert (await client.get("/api/workspace")).json()["name"] == "Workspace of boris@example.com"


async def test_admin_has_no_workspace(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    assert (await client.get("/api/workspace")).status_code == 403


async def test_login_accepts_only_json(client: AsyncClient, make_user: MakeUser):
    # A cross-site HTML form can send text/plain or form data, never application/json
    # without a CORS preflight: rejecting other content types blocks login CSRF.
    await make_user("anna@example.com")
    response = await client.post(
        "/api/auth/login",
        content=f'{{"email": "anna@example.com", "password": "{PASSWORD}"}}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 422
    assert SESSION_COOKIE not in client.cookies

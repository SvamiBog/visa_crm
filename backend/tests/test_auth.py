import asyncio
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.auth import MAX_FAILED_LOGINS
from app.api.deps import SESSION_COOKIE
from app.core import security
from tests.helpers import CLOCK, NEW_PASSWORD, PASSWORD, MakeUser, enable_two_factor, login


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


async def test_wrong_passwords_sent_at_once_still_lock_the_account(
    client: AsyncClient, make_user: MakeUser
):
    # Requests running side by side must not read the same counter and lose each other's
    # misses: a burst tries no more passwords than attempts made one by one.
    await make_user("anna@example.com")
    burst = 2 * MAX_FAILED_LOGINS
    misses = await asyncio.gather(
        *(login(client, "anna@example.com", f"wrong-{n}-password") for n in range(burst))
    )
    statuses = [r.status_code for r in misses]
    assert statuses.count(401) == MAX_FAILED_LOGINS
    assert statuses.count(423) == burst - MAX_FAILED_LOGINS
    assert (await login(client, "anna@example.com")).status_code == 423


class CountingHasher:
    """The password hasher, noting each hash a password is checked against."""

    def __init__(self, hasher) -> None:
        self.hasher = hasher
        self.checked: list[str] = []

    def verify(self, password_hash: str, password: str) -> bool:
        self.checked.append(password_hash)
        return self.hasher.verify(password_hash, password)


async def test_unknown_email_is_checked_against_a_hash_too(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    # Otherwise an unknown email answers faster than a wrong password, and the timing
    # tells which addresses have an account.
    hasher = CountingHasher(security._hasher)
    monkeypatch.setattr(security, "_hasher", hasher)
    assert (await login(client, "ghost@example.com")).status_code == 401
    assert hasher.checked == [security._DUMMY_HASH]


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
    secret, _ = await enable_two_factor(client, NEW_PASSWORD)

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


# The settings forms that ask for the current password: URL, the body with the given
# password, and whether the user has two-factor login on (to turn it off) or not.
PASSWORD_FORMS: dict[str, tuple[str, Callable[[str], dict], bool]] = {
    "change password": (
        "/api/auth/change-password",
        lambda password: {"current_password": password, "new_password": NEW_PASSWORD},
        False,
    ),
    "change email": (
        "/api/auth/email-change",
        lambda password: {"new_email": "anna.new@example.com", "current_password": password},
        False,
    ),
    "turn on two-factor login": (
        "/api/auth/two-factor/setup",
        lambda password: {"password": password},
        False,
    ),
    "turn off two-factor login": (
        "/api/auth/two-factor/disable",
        lambda password: {"password": password, "code": CLOCK.next_code()},
        True,
    ),
}


@pytest.mark.parametrize("form", PASSWORD_FORMS)
async def test_guessing_the_password_in_settings_locks_the_account_and_signs_out(
    client: AsyncClient, make_user: MakeUser, form: str
):
    # A stolen session must not try passwords without limit: misses count with wrong
    # logins, and the miss that locks the account also ends every session of it.
    url, body, two_factor = PASSWORD_FORMS[form]
    await make_user("anna@example.com", two_factor=two_factor)
    await login(client, "anna@example.com")
    other_device = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login(other_device, "anna@example.com")

    misses = [
        await client.post(url, json=body(f"guess-{n:04d}-password"))
        for n in range(MAX_FAILED_LOGINS)
    ]
    assert [r.json()["detail"] for r in misses] == ["invalid_current_password"] * (
        MAX_FAILED_LOGINS - 1
    ) + ["account_locked"]
    assert misses[-1].status_code == 423
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await other_device.get("/api/auth/me")).status_code == 401
    client.cookies.clear()
    assert (await login(client, "anna@example.com")).status_code == 423
    await other_device.aclose()


@pytest.mark.parametrize("form", PASSWORD_FORMS)
async def test_settings_check_no_password_while_the_account_is_locked(
    client: AsyncClient, make_user: MakeUser, form: str
):
    # Locked by wrong logins elsewhere: a session left open gets no more tries, not even
    # with the right password, and is ended.
    url, body, two_factor = PASSWORD_FORMS[form]
    await make_user("anna@example.com", two_factor=two_factor)
    await login(client, "anna@example.com")
    stranger = AsyncClient(transport=client._transport, base_url=client.base_url)
    for _ in range(MAX_FAILED_LOGINS):
        await login(stranger, "anna@example.com", "nope")
    await stranger.aclose()

    locked = await client.post(url, json=body(PASSWORD))
    assert locked.status_code == 423 and locked.json() == {"detail": "account_locked"}
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

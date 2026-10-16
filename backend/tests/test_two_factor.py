import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.auth import MAX_FAILED_LOGINS
from app.api.deps import SESSION_COOKIE
from app.cli import reset_user_two_factor
from app.core import two_factor
from app.core.crypto import generate_master_key
from app.core.two_factor import verify_totp
from tests.helpers import (
    CLOCK,
    PASSWORD,
    TOTP_SECRET,
    MakeUser,
    enable_two_factor,
    login,
    login_first_step,
)


async def test_password_alone_does_not_sign_in(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    first = await login_first_step(client, "anna@example.com")
    assert first.json() == {"mfa_required": True}
    assert SESSION_COOKIE in client.cookies
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/workspace")).status_code == 401

    verified = await client.post("/api/auth/login/verify", json={"code": CLOCK.next_code()})
    assert verified.status_code == 200
    assert verified.json()["two_factor_enabled"] is True
    assert (await client.get("/api/workspace")).status_code == 200


async def test_wrong_code_is_rejected(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login_first_step(client, "anna@example.com")
    wrong = await client.post("/api/auth/login/verify", json={"code": "000000"})
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "invalid_code"}
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_code_cannot_be_used_twice(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    code = CLOCK.next_code()
    await login_first_step(client, "anna@example.com")
    assert (await client.post("/api/auth/login/verify", json={"code": code})).status_code == 200
    await client.post("/api/auth/logout")

    await login_first_step(client, "anna@example.com")
    replay = await client.post("/api/auth/login/verify", json={"code": code})
    assert replay.json() == {"detail": "invalid_code"}


async def test_code_guessing_locks_account_even_with_correct_password(
    client: AsyncClient, make_user: MakeUser
):
    # Re-entering the right password between guesses must not reset the counter.
    await make_user("anna@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        assert (await login_first_step(client, "anna@example.com")).status_code == 200
        await client.post("/api/auth/login/verify", json={"code": "000000"})
    locked = await login_first_step(client, "anna@example.com")
    assert locked.status_code == 423


async def test_pending_login_expires(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login_first_step(client, "anna@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE user_sessions SET expires_at = now() - interval '1 s'"))
    expired = await client.post("/api/auth/login/verify", json={"code": CLOCK.next_code()})
    assert expired.json() == {"detail": "login_expired"}


async def test_two_factor_is_optional_and_set_up_on_wish(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com", two_factor=False)
    me = (await login(client, "anna@example.com")).json()
    assert me["two_factor_enabled"] is False and "must_set_up_two_factor" not in me
    assert (await client.get("/api/workspace")).status_code == 200

    setup = (await client.post("/api/auth/two-factor/setup")).json()
    assert setup["qr_code"].startswith("data:image/svg+xml")
    assert setup["otpauth_uri"].startswith("otpauth://totp/VisaCRM:anna%40example.com?")
    wrong = await client.post("/api/auth/two-factor/enable", json={"code": "000000"})
    assert wrong.json() == {"detail": "invalid_code"}

    code = CLOCK.next_code(setup["secret"])
    enabled = await client.post("/api/auth/two-factor/enable", json={"code": code})
    codes = enabled.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert (await client.get("/api/workspace")).status_code == 200
    again = await client.post("/api/auth/two-factor/setup")
    assert again.json() == {"detail": "two_factor_already_enabled"}


async def test_admin_may_work_without_two_factor(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin", two_factor=False)
    me = (await login(client, "root@example.com")).json()
    assert me["two_factor_enabled"] is False
    assert (await client.get("/api/admin/specialists")).status_code == 200


async def test_two_factor_is_turned_off_with_password_and_code(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    url = "/api/auth/two-factor/disable"
    not_on = await client.post(url, json={"password": PASSWORD, "code": "000000"})
    assert not_on.json() == {"detail": "two_factor_not_enabled"}
    secret, codes = await enable_two_factor(client)

    wrong_password = await client.post(url, json={"password": "nope-nope", "code": "000000"})
    assert wrong_password.json() == {"detail": "invalid_current_password"}
    wrong_code = await client.post(url, json={"password": PASSWORD, "code": "000000"})
    assert wrong_code.json() == {"detail": "invalid_code"}
    # A recovery code works too (the phone may be lost).
    off = await client.post(url, json={"password": PASSWORD, "code": codes[0]})
    assert off.status_code == 204
    me = (await client.get("/api/auth/me")).json()
    assert me["two_factor_enabled"] is False

    # Next login is by password only; the old codes are gone.
    await client.post("/api/auth/logout")
    assert (await login(client, "anna@example.com")).json()["two_factor_enabled"] is False
    events = (await client.get("/api/audit")).json()["events"]
    assert any(e["action"] == "auth.two_factor_disabled" for e in events)


async def test_recovery_code_works_once(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    _, codes = await enable_two_factor(client)
    await client.post("/api/auth/logout")

    await login_first_step(client, "anna@example.com")
    used = await client.post("/api/auth/login/verify", json={"code": codes[0].upper()})
    assert used.status_code == 200
    await client.post("/api/auth/logout")

    await login_first_step(client, "anna@example.com")
    again = await client.post("/api/auth/login/verify", json={"code": codes[0]})
    assert again.json() == {"detail": "invalid_code"}


async def test_secret_and_recovery_codes_are_not_stored_in_clear(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    secret, codes = await enable_two_factor(client)
    async with owner_engine.connect() as conn:
        stored = await conn.scalar(text("SELECT totp_secret_encrypted FROM users"))
        hashes = (await conn.execute(text("SELECT code_hash FROM user_recovery_codes"))).scalars()
        hashes = set(hashes)
    assert secret.encode() not in stored
    assert not hashes & {c for c in codes} and not hashes & {c.replace("-", "") for c in codes}


async def test_cli_reset_turns_two_factor_off(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await reset_user_two_factor("root@example.com")
    me = (await login(client, "root@example.com")).json()
    assert me["two_factor_enabled"] is False


async def test_rotation_keeps_two_factor_working(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, master_key
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.db import scope_to_system
    from app.core.vault import reseal_with_current_master_key

    old, new = generate_master_key(), generate_master_key()
    master_key(old)
    await make_user("anna@example.com")
    master_key(new, previous=old)
    async with async_sessionmaker(owner_engine)() as session:
        scope_to_system(session)
        assert await reseal_with_current_master_key(session) == 1
        await session.commit()
    master_key(new)
    assert (await login(client, "anna@example.com")).status_code == 200


@pytest.mark.parametrize(
    ("offset_steps", "accepted"), [(-2, False), (-1, True), (0, True), (1, True), (2, False)]
)
def test_clock_drift_of_one_step_is_accepted(
    monkeypatch: pytest.MonkeyPatch, offset_steps: int, accepted: bool
):
    now = 1_900_000_000.0
    monkeypatch.setattr(two_factor, "_now", lambda: now)
    code = pyotp.TOTP(TOTP_SECRET).at(now + offset_steps * 30)
    assert (verify_totp(TOTP_SECRET, code, None) is not None) is accepted


def test_malformed_codes_are_rejected():
    assert verify_totp(TOTP_SECRET, "12345", None) is None
    assert verify_totp(TOTP_SECRET, "abcdef", None) is None

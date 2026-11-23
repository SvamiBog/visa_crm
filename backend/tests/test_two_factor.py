import asyncio
import hashlib

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.auth import MAX_FAILED_LOGINS
from app.api.deps import SESSION_COOKIE
from app.cli import reset_user_two_factor, upgrade_recovery_codes
from app.core import two_factor
from app.core.config import get_settings
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
    # Locked: the right password answers as for an address without an account (L4).
    locked = await login_first_step(client, "anna@example.com")
    assert locked.status_code == 401 and locked.json() == {"detail": "invalid_credentials"}


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

    setup = (await client.post("/api/auth/two-factor/setup", json={"password": PASSWORD})).json()
    assert setup["qr_code"].startswith("data:image/svg+xml")
    assert setup["otpauth_uri"].startswith("otpauth://totp/VisaCRM:anna%40example.com?")
    wrong = await client.post("/api/auth/two-factor/enable", json={"code": "000000"})
    assert wrong.json() == {"detail": "invalid_code"}

    code = CLOCK.next_code(setup["secret"])
    enabled = await client.post("/api/auth/two-factor/enable", json={"code": code})
    codes = enabled.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert (await client.get("/api/workspace")).status_code == 200
    again = await client.post("/api/auth/two-factor/setup", json={"password": PASSWORD})
    assert again.json() == {"detail": "two_factor_already_enabled"}


async def test_turning_two_factor_on_needs_the_password(client: AsyncClient, make_user: MakeUser):
    # Otherwise a stolen session could put the thief's own authenticator on the account
    # and keep its recovery codes.
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    url = "/api/auth/two-factor/setup"
    assert (await client.post(url)).status_code == 422
    wrong = await client.post(url, json={"password": "nope-nope"})
    assert wrong.status_code == 400 and wrong.json() == {"detail": "invalid_current_password"}
    # No secret was made for a wrong password: there is nothing to confirm.
    enable = await client.post("/api/auth/two-factor/enable", json={"code": "000000"})
    assert enable.json() == {"detail": "two_factor_setup_not_started"}

    right = await client.post(url, json={"password": PASSWORD})
    assert right.status_code == 200 and right.json()["secret"]
    events = (await client.get("/api/audit")).json()["events"]
    failed = [e for e in events if e["action"] == "auth.login_failed"]
    assert [e["details"] for e in failed] == [{"reason": "two_factor_setup_password"}]


async def test_wrong_codes_when_turning_two_factor_off_lock_the_account_and_sign_out(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    url = "/api/auth/two-factor/disable"
    misses = [
        await client.post(url, json={"password": PASSWORD, "code": "000000"})
        for _ in range(MAX_FAILED_LOGINS)
    ]
    assert [r.json()["detail"] for r in misses] == ["invalid_code"] * (MAX_FAILED_LOGINS - 1) + [
        "account_locked"
    ]
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await login(client, "anna@example.com")).status_code == 401


async def _recovery_codes_stored(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as conn:
        return await conn.scalar(text("SELECT count(*) FROM user_recovery_codes"))


async def test_wrong_codes_when_turning_two_factor_on_lock_the_account_and_sign_out(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Audit h02.1: a stolen session must not guess the code of a setup the owner began
    # and left: that would turn 2FA on with the thief holding the recovery codes.
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    other_device = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login(other_device, "anna@example.com")
    assert (await client.post("/api/auth/two-factor/setup", json={"password": PASSWORD})).is_success

    url = "/api/auth/two-factor/enable"
    misses = [await client.post(url, json={"code": "000000"}) for _ in range(MAX_FAILED_LOGINS)]
    assert [r.json()["detail"] for r in misses] == ["invalid_code"] * (MAX_FAILED_LOGINS - 1) + [
        "account_locked"
    ]
    assert misses[-1].status_code == 423
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await other_device.get("/api/auth/me")).status_code == 401
    await other_device.aclose()
    async with owner_engine.connect() as conn:
        reasons = (
            await conn.execute(
                text("SELECT details->>'reason' FROM audit_events WHERE action = :a"),
                {"a": "auth.login_failed"},
            )
        ).scalars()
        assert list(reasons) == ["code"] * MAX_FAILED_LOGINS
        assert await conn.scalar(text("SELECT totp_enabled_at FROM users")) is None
    assert await _recovery_codes_stored(owner_engine) == 0


async def test_wrong_codes_sent_at_once_when_turning_two_factor_on_are_all_counted(
    client: AsyncClient, make_user: MakeUser
):
    # The row is locked while a code is checked: misses sent at once take turns and none
    # of them gets past the tenth.
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    await client.post("/api/auth/two-factor/setup", json={"password": PASSWORD})
    burst = 2 * MAX_FAILED_LOGINS
    answers = await asyncio.gather(
        *(client.post("/api/auth/two-factor/enable", json={"code": "000000"}) for _ in range(burst))
    )
    statuses = [a.status_code for a in answers]
    assert statuses.count(400) == MAX_FAILED_LOGINS - 1
    assert set(statuses) <= {400, 401, 423} and 423 in statuses


async def test_two_factor_turned_on_twice_at_once_keeps_one_set_of_recovery_codes(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    setup = await client.post("/api/auth/two-factor/setup", json={"password": PASSWORD})
    code = CLOCK.next_code(setup.json()["secret"])
    # Open connections first, so that neither request finishes while the other connects.
    await asyncio.gather(*(client.get("/api/auth/me") for _ in range(4)))
    answers = await asyncio.gather(
        *(client.post("/api/auth/two-factor/enable", json={"code": code}) for _ in range(2))
    )
    assert sorted(a.status_code for a in answers) == [200, 409]
    assert await _recovery_codes_stored(owner_engine) == 10


@pytest.mark.parametrize("turn", ["on", "off"])
async def test_turning_two_factor_on_or_off_signs_out_other_devices(
    client: AsyncClient, make_user: MakeUser, turn: str
):
    # Audit h01.2: a session stolen before the change must not outlive it. The device
    # that made the change stays signed in.
    await make_user("anna@example.com", two_factor=turn == "off")
    other_device = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login(other_device, "anna@example.com")
    halfway = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login_first_step(halfway, "anna@example.com")
    await login(client, "anna@example.com")

    if turn == "on":
        await enable_two_factor(client)
    else:
        off = await client.post(
            "/api/auth/two-factor/disable", json={"password": PASSWORD, "code": CLOCK.next_code()}
        )
        assert off.status_code == 204
        # A login left at its second step ends too.
        verify = await halfway.post("/api/auth/login/verify", json={"code": CLOCK.next_code()})
        assert verify.json() == {"detail": "login_expired"}
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await other_device.get("/api/auth/me")).status_code == 401
    await other_device.aclose()
    await halfway.aclose()


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
    # Audit h02.7: digits of another script are a wrong code, not a server error, even
    # when they spell the right one.
    right = pyotp.TOTP(TOTP_SECRET).at(two_factor._now())
    for digits in (ARABIC_INDIC, FULLWIDTH):
        assert verify_totp(TOTP_SECRET, right.translate(digits), None) is None


ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
FULLWIDTH = str.maketrans("0123456789", "０１２３４５６７８９")


async def _failed_logins(owner_engine: AsyncEngine, email: str) -> int:
    async with owner_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT failed_login_count FROM users WHERE email = :e"), {"e": email}
        )


async def test_a_code_in_digits_of_another_script_is_a_wrong_code_that_counts(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login_first_step(client, "anna@example.com")
    code = CLOCK.next_code().translate(ARABIC_INDIC)
    response = await client.post("/api/auth/login/verify", json={"code": code})
    assert (response.status_code, response.json()) == (401, {"detail": "invalid_code"})
    assert await _failed_logins(owner_engine, "anna@example.com") == 1


@pytest.mark.parametrize("reset_by", ["admin", "specialist", "cli"])
async def test_a_login_left_at_its_code_ends_when_two_factor_is_reset(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, reset_by: str
):
    # Audit h02.8: the login waiting for its code must not outlive the 2FA it waits for.
    tenant_id = await make_user("anna@example.com")
    await make_user("root@example.com", role="admin")
    await make_user("staff@example.com", role="employee", tenant_id=tenant_id, two_factor=True)
    whose = "anna@example.com" if reset_by == "admin" else "staff@example.com"
    halfway = AsyncClient(transport=client._transport, base_url=client.base_url)
    assert (await login_first_step(halfway, whose)).json() == {"mfa_required": True}

    if reset_by == "admin":
        await login(client, "root@example.com")
        reset = await client.post(f"/api/admin/specialists/{tenant_id}/reset-password")
        assert reset.status_code == 200
    elif reset_by == "specialist":
        await login(client, "anna@example.com")
        staff_id = (await client.get("/api/employees")).json()[0]["id"]
        assert (await client.post(f"/api/employees/{staff_id}/reset-password")).status_code == 200
    else:
        await reset_user_two_factor(whose)

    verify = await halfway.post("/api/auth/login/verify", json={"code": CLOCK.next_code()})
    assert verify.json() == {"detail": "login_expired"}
    await halfway.aclose()


@pytest.fixture
def server_key(monkeypatch: pytest.MonkeyPatch):
    """Set SECRET_KEY and ENV for one test."""

    def set_key(value: str, env: str = "test") -> None:
        monkeypatch.setenv("SECRET_KEY", value)
        monkeypatch.setenv("ENV", env)
        get_settings.cache_clear()

    yield set_key
    get_settings.cache_clear()


def _unsalted(code: str) -> str:
    """How recovery codes were kept before backlog S16."""
    return hashlib.sha256(code.replace("-", "").encode()).hexdigest()


async def _stored_codes(owner_engine: AsyncEngine) -> set[str]:
    async with owner_engine.connect() as conn:
        return set(
            (await conn.execute(text("SELECT code_hash FROM user_recovery_codes"))).scalars()
        )


async def _add_old_codes(owner_engine: AsyncEngine, email: str, *codes: str) -> None:
    async with owner_engine.begin() as conn:
        for code in codes:
            await conn.execute(
                text(
                    "INSERT INTO user_recovery_codes (id, user_id, code_hash) "
                    "SELECT gen_random_uuid(), id, :h FROM users WHERE email = :e"
                ),
                {"h": _unsalted(code), "e": email},
            )


async def test_recovery_codes_are_kept_as_hmac_with_the_server_key(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, server_key
):
    # Audit h02.3: an unsalted SHA-256 of a code (about 49.5 bits) is guessed offline from
    # a copy of the database; without the server key a copy gives nothing to guess against.
    server_key("a-server-key")
    await make_user("anna@example.com", two_factor=False)
    await login(client, "anna@example.com")
    _, codes = await enable_two_factor(client)
    stored = await _stored_codes(owner_engine)
    assert not stored & {_unsalted(c) for c in codes}
    assert stored == {two_factor.hash_recovery_code(c) for c in codes}

    server_key("another-server-key")
    assert not stored & {two_factor.hash_recovery_code(c) for c in codes}


async def test_an_old_unsalted_recovery_code_is_taken_once_and_kept_as_hmac(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await _add_old_codes(owner_engine, "anna@example.com", "abcde-fghjk")
    await login_first_step(client, "anna@example.com")
    used = await client.post("/api/auth/login/verify", json={"code": "ABCDE-FGHJK"})
    assert used.status_code == 200
    async with owner_engine.connect() as conn:
        row = (await conn.execute(text("SELECT code_hash, used_at FROM user_recovery_codes"))).one()
    assert row.code_hash != _unsalted("abcde-fghjk")
    assert row.code_hash == two_factor.hash_recovery_code("abcde-fghjk")
    assert row.used_at is not None

    await client.post("/api/auth/logout")
    await login_first_step(client, "anna@example.com")
    again = await client.post("/api/auth/login/verify", json={"code": "abcde-fghjk"})
    assert again.json() == {"detail": "invalid_code"}


async def test_the_cli_keeps_every_old_recovery_code_as_hmac(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Codes never used would otherwise stay guessable in the database: the command keys
    # the stored SHA-256 itself, the codes are not needed for it.
    await make_user("anna@example.com")
    codes = ("abcde-fghjk", "mnpqr-stuvw")
    await _add_old_codes(owner_engine, "anna@example.com", *codes)
    assert await upgrade_recovery_codes() == 2
    assert await _stored_codes(owner_engine) == {two_factor.hash_recovery_code(c) for c in codes}
    assert await upgrade_recovery_codes() == 0

    await login_first_step(client, "anna@example.com")
    used = await client.post("/api/auth/login/verify", json={"code": codes[1]})
    assert used.status_code == 200


@pytest.mark.parametrize("value", ["", "change-me"])
def test_recovery_codes_need_a_server_key_outside_development(server_key, value: str):
    server_key(value, env="production")
    with pytest.raises(two_factor.RecoveryKeyMissing):
        two_factor.hash_recovery_code("abcde-fghjk")
    server_key("a-server-key", env="production")
    assert two_factor.hash_recovery_code("abcde-fghjk") != _unsalted("abcde-fghjk")

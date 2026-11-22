"""Changing the login email with a code sent to the new address."""

import asyncio
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.email_change import MAX_CODE_ATTEMPTS, MAX_CODES_A_DAY
from app.core import mail
from app.core.config import get_settings
from tests.helpers import PASSWORD, MakeUser, login

URL = "/api/auth/email-change"


@pytest.fixture(autouse=True)
def outbox():
    mail.OUTBOX.clear()
    yield mail.OUTBOX
    mail.OUTBOX.clear()


def code_in(letter: mail.Mail) -> str:
    match = re.search(r"\b(\d{6})\b", letter.text)
    assert match, letter.text
    return match.group(1)


async def ask(client: AsyncClient, new_email: str, password: str = PASSWORD):
    return await client.post(URL, json={"new_email": new_email, "current_password": password})


def wrong_codes(real: str, count: int) -> list[str]:
    """Distinct six-digit codes, none of them the one in the letter."""
    return [c for c in (f"{n:06d}" for n in range(200_000, 200_000 + count + 1)) if c != real][
        :count
    ]


async def let_the_pause_pass(owner_engine: AsyncEngine) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE email_changes SET sent_at = sent_at - interval '61 s'"))


async def test_email_changes_after_the_code_from_the_new_address(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail]
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    other_device = AsyncClient(transport=client._transport, base_url=client.base_url)
    await login(other_device, "anna@example.com")

    asked = await ask(client, "  Anna.New@Example.com ")
    assert asked.status_code == 200, asked.text
    assert asked.json()["new_email"] == "anna.new@example.com"
    [letter] = outbox
    assert letter.to == "anna.new@example.com"
    assert (await client.get(URL)).json()["new_email"] == "anna.new@example.com"
    # Nothing changes until the code is entered.
    assert (await client.get("/api/auth/me")).json()["email"] == "anna@example.com"

    wrong = await client.post(f"{URL}/confirm", json={"code": "000000"})
    if code_in(letter) != "000000":
        assert wrong.status_code == 400 and wrong.json()["detail"] == "invalid_code"
    confirmed = await client.post(f"{URL}/confirm", json={"code": f" {code_in(letter)} "})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["email"] == "anna.new@example.com"
    assert (await client.get(URL)).json() is None

    # The old address is told, without the full new one.
    notice = outbox[-1]
    assert notice.to == "anna@example.com"
    assert "an***@example.com" in notice.text and "anna.new@" not in notice.text
    # Other devices are signed out; the new email logs in, the old one does not.
    assert (await other_device.get("/api/auth/me")).status_code == 401
    client.cookies.clear()
    assert (await login(client, "anna@example.com")).status_code == 401
    assert (await login(client, "anna.new@example.com")).status_code == 200

    events = [e["action"] for e in (await client.get("/api/audit")).json()["events"]]
    assert "auth.email_changed" in events and "auth.email_change_requested" in events
    await other_device.aclose()


async def test_request_needs_the_password_and_a_free_new_address(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail]
):
    await make_user("anna@example.com")
    await make_user("boris@example.com")
    await login(client, "anna@example.com")

    wrong = await ask(client, "new@example.com", "not-the-password")
    assert wrong.status_code == 400 and wrong.json()["detail"] == "invalid_current_password"
    same = await ask(client, "ANNA@example.com")
    assert same.status_code == 400 and same.json()["detail"] == "email_unchanged"
    taken = await ask(client, "boris@example.com")
    assert taken.status_code == 409 and taken.json()["detail"] == "email_taken"
    bad = await ask(client, "not-an-email")
    assert bad.status_code == 422
    assert outbox == []

    assert (await ask(client, "new@example.com")).status_code == 200
    again = await ask(client, "new@example.com")
    assert again.status_code == 429 and again.json()["detail"] == "code_sent_recently"
    cancelled = await client.delete(URL)
    assert cancelled.status_code == 204 and (await client.get(URL)).json() is None
    # Cancelling does not lift the pause before the next code.
    assert (await ask(client, "other@example.com")).status_code == 429


async def test_guessing_the_code_ends_the_change(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail]
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await ask(client, "new@example.com")
    real = code_in(outbox[0])
    guesses = [c for c in ("111111", "222222", "333333", "444444", "555555", "666666") if c != real]
    for guess in guesses[:4]:
        response = await client.post(f"{URL}/confirm", json={"code": guess})
        assert response.json()["detail"] == "invalid_code"
    fifth = await client.post(f"{URL}/confirm", json={"code": guesses[4]})
    assert fifth.status_code == 400 and fifth.json()["detail"] == "code_expired"
    late = await client.post(f"{URL}/confirm", json={"code": real})
    assert late.json()["detail"] == "code_expired"


async def test_wrong_codes_sent_at_once_still_burn_the_code(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail]
):
    # Confirms running side by side must not read the same counter and lose each other's
    # misses: a burst checks no more codes than guesses made one by one.
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await ask(client, "new@example.com")
    real = code_in(outbox[0])
    burst = 8
    answers = await asyncio.gather(
        *(client.post(f"{URL}/confirm", json={"code": c}) for c in wrong_codes(real, burst))
    )
    details = sorted(r.json()["detail"] for r in answers)
    misses_left = MAX_CODE_ATTEMPTS - 1
    assert details == ["code_expired"] * (burst - misses_left) + ["invalid_code"] * misses_left

    late = await client.post(f"{URL}/confirm", json={"code": real})
    assert late.status_code == 400 and late.json()["detail"] == "code_expired"
    assert (await client.get("/api/auth/me")).json()["email"] == "anna@example.com"
    assert (await client.get(URL)).json() is None


async def test_a_burnt_code_keeps_the_pause_before_the_next_one(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail], owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await ask(client, "new@example.com")
    for guess in wrong_codes(code_in(outbox[0]), MAX_CODE_ATTEMPTS):
        last = await client.post(f"{URL}/confirm", json={"code": guess})
    assert last.json()["detail"] == "code_expired"

    again = await ask(client, "new@example.com")
    assert again.status_code == 429 and again.json()["detail"] == "code_sent_recently"
    assert len(outbox) == 1
    # After the pause a new code comes, with all its attempts.
    await let_the_pause_pass(owner_engine)
    assert (await ask(client, "new@example.com")).status_code == 200
    confirmed = await client.post(f"{URL}/confirm", json={"code": code_in(outbox[-1])})
    assert confirmed.status_code == 200 and confirmed.json()["email"] == "new@example.com"


async def test_code_letters_a_day_are_limited(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail], owner_engine: AsyncEngine
):
    # Each letter goes to an address nobody has confirmed: one user cannot mail on and on.
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    async with owner_engine.begin() as conn:
        # Letters sent more than 24 hours ago no longer count.
        await conn.execute(
            text(
                "INSERT INTO audit_events (actor_id, action, occurred_at) "
                "SELECT id, 'auth.email_change_requested', now() - interval '25 hours' "
                "FROM users CROSS JOIN generate_series(1, :n) WHERE email = 'anna@example.com'"
            ),
            {"n": MAX_CODES_A_DAY},
        )
    for n in range(MAX_CODES_A_DAY):
        sent = await ask(client, f"new{n}@example.com")
        assert sent.status_code == 200, sent.text
        await let_the_pause_pass(owner_engine)

    over = await ask(client, "one-more@example.com")
    assert over.status_code == 429 and over.json()["detail"] == "code_sent_recently"
    assert len(outbox) == MAX_CODES_A_DAY
    assert "one-more@example.com" not in [letter.to for letter in outbox]
    # The last code still works: the limit stops new letters, not the change under way.
    confirmed = await client.post(f"{URL}/confirm", json={"code": code_in(outbox[-1])})
    assert confirmed.json()["email"] == f"new{MAX_CODES_A_DAY - 1}@example.com"


async def test_an_expired_code_does_not_change_the_email(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail], owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await ask(client, "new@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE email_changes SET expires_at = now() - interval '1 second'")
        )
    late = await client.post(f"{URL}/confirm", json={"code": code_in(outbox[0])})
    assert late.status_code == 400 and late.json()["detail"] == "code_expired"


async def test_address_taken_while_waiting_for_the_code(
    client: AsyncClient, make_user: MakeUser, outbox: list[mail.Mail]
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await ask(client, "new@example.com")
    await make_user("new@example.com")
    taken = await client.post(f"{URL}/confirm", json={"code": code_in(outbox[0])})
    assert taken.status_code == 409 and taken.json()["detail"] == "email_taken"
    assert (await client.get("/api/auth/me")).json()["email"] == "anna@example.com"


async def test_without_a_mail_server_outside_development_nothing_is_sent(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        response = await ask(client, "new@example.com")
    finally:
        monkeypatch.delenv("ENV")
        get_settings.cache_clear()
    assert response.status_code == 503 and response.json()["detail"] == "mail_unavailable"
    assert (await client.get(URL)).json() is None

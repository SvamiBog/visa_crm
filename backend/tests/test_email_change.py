"""Changing the login email with a code sent to the new address."""

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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

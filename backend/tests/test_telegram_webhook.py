"""The client bot's webhook (backlog 2.12.0, app/api/telegram.py): Telegram calls
/api/telegram/webhook with every message written to the bot.

Only Telegram may: it repeats the secret the webhook was set with in a header. Switched off
(CLIENT_BOT, or a key missing) the address answers «not found». The body is limited like
any form and read only after the secret is checked. The bot answers /start with a greeting
and nothing else yet (finding the client's card is 2.12.1). Who wrote and what never
reaches the log, nor do the keys.
"""

import json
import logging
from collections.abc import AsyncIterator, Callable

import pytest
from httpx import AsyncClient, Response

from app.core.client_bot import GREETING
from app.core.request_limit import MAX_FORM_BYTES
from tests.support.helpers import (
    BOT_TOKEN,
    TELEGRAM_CHAT,
    TELEGRAM_PERSON,
    WEBHOOK_SECRET,
    FakeTelegram,
    telegram_message,
)

WEBHOOK = "/api/telegram/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# The site's modules that take part in the bot: what they log is what the tests read.
BOT_LOGGERS = ("app.api.telegram", "app.core.client_bot", "app.core.telegram")


async def call(
    client: AsyncClient, update: object = None, *, secret: str | None = WEBHOOK_SECRET
) -> Response:
    """Telegram's call with an update; `secret=None` sends no secret header."""
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[SECRET_HEADER] = secret
    body = update if isinstance(update, bytes) else json.dumps(update or telegram_message())
    return await client.post(WEBHOOK, content=body, headers=headers)


@pytest.fixture
def bot_log(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> str:
    """Everything the site logs while the test runs, from the lowest level."""
    # The migrations of the test database run in this process, and Alembic's logging config
    # switches off the loggers made before it.
    for name in BOT_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    caplog.set_level(logging.DEBUG)
    return caplog


# --- who may call ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        None,
        "",
        "a-guess",
        WEBHOOK_SECRET.upper(),
        WEBHOOK_SECRET[:-1],
        WEBHOOK_SECRET + "0",
        BOT_TOKEN,
        "секрет",  # not the Latin letters a header may carry
    ],
)
async def test_without_telegrams_secret_the_webhook_refuses(
    client: AsyncClient, bot_keys: None, telegram_api: FakeTelegram, secret: str | None
) -> None:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[SECRET_HEADER] = secret.encode()  # as bytes: httpx sends them as they are
    refused = await client.post(WEBHOOK, content=json.dumps(telegram_message()), headers=headers)
    assert refused.status_code == 403 and refused.json() == {"detail": "forbidden"}
    assert telegram_api.calls == []


async def test_the_secret_is_checked_before_the_body_is_read(
    client: AsyncClient, bot_keys: None
) -> None:
    read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield json.dumps(telegram_message()).encode()

    refused = await client.post(WEBHOOK, content=body(), headers={SECRET_HEADER: "a-guess"})
    assert refused.status_code == 403
    assert not read


@pytest.mark.parametrize(
    "variables",
    [
        {"CLIENT_BOT": "false"},
        {"TELEGRAM_BOT_TOKEN": None},
        {"TELEGRAM_WEBHOOK_SECRET": None},
        {"TELEGRAM_BOT_TOKEN": None, "TELEGRAM_WEBHOOK_SECRET": None},
    ],
)
async def test_switched_off_or_without_keys_the_webhook_is_not_there(
    client: AsyncClient,
    bot_keys: None,
    environment: Callable[..., None],
    telegram_api: FakeTelegram,
    variables: dict[str, str | None],
) -> None:
    environment(**variables)
    # With Telegram's secret, with none, and with the empty one that would equal a secret
    # that is not set: FastAPI's own «not found», nothing of the bot's.
    for secret in (WEBHOOK_SECRET, None, ""):
        answer = await call(client, secret=secret)
        assert answer.status_code == 404 and answer.json() == {"detail": "Not Found"}, secret
    assert telegram_api.calls == []


async def test_in_production_the_webhook_waits_for_the_owners_switch(
    client: AsyncClient,
    bot_keys: None,
    environment: Callable[..., None],
    telegram_api: FakeTelegram,
) -> None:
    environment(ENV="production")
    assert (await call(client)).status_code == 404
    assert telegram_api.calls == []
    environment(CLIENT_BOT="true")
    assert (await call(client)).status_code == 200
    assert [method for method, _ in telegram_api.calls] == ["sendMessage"]


# --- what the bot does with an update -------------------------------------------------------


@pytest.mark.parametrize(("language", "greeting"), [("ru", "ru"), ("en", "en"), (None, "ru")])
async def test_start_is_answered_with_the_greeting(
    client: AsyncClient,
    bot_keys: None,
    telegram_api: FakeTelegram,
    language: str | None,
    greeting: str,
) -> None:
    taken = await call(client, telegram_message("/start", language=language))
    assert taken.status_code == 200
    assert telegram_api.urls == [f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"]
    assert telegram_api.calls == [
        ("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": GREETING[greeting]})
    ]


@pytest.mark.parametrize(
    "update",
    [
        telegram_message("Здравствуйте, я от Анны"),
        telegram_message("/start", chat="group"),
        {"update_id": 900002, "edited_message": telegram_message()["message"]},
        {"update_id": 900003, "callback_query": {"id": "1", "data": "sign"}},
        {"update_id": 900004},
    ],
)
async def test_any_other_update_is_taken_and_left_unanswered(
    client: AsyncClient, bot_keys: None, telegram_api: FakeTelegram, update: dict
) -> None:
    # Taken, or Telegram would send it again and again.
    assert (await call(client, update)).status_code == 200
    assert telegram_api.calls == []


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"\xff\xfe\x00",
        b"[1, 2]",
        b'"/start"',
        b"null",
        b"[" * 50_000,  # nested deeper than the decoder goes
    ],
    ids=["empty", "text", "not-utf8", "list", "string", "null", "nested"],
)
async def test_a_body_that_is_not_an_update_is_dropped_without_an_error(
    client: AsyncClient, bot_keys: None, telegram_api: FakeTelegram, body: bytes
) -> None:
    assert (await call(client, body)).status_code == 200
    assert telegram_api.calls == []


async def test_a_body_over_the_limit_is_refused_before_it_is_read(
    client: AsyncClient, bot_keys: None, telegram_api: FakeTelegram
) -> None:
    update = telegram_message("/start " + "x" * MAX_FORM_BYTES)
    declared = await call(client, update)
    assert declared.status_code == 413 and declared.json() == {"detail": "request_too_large"}

    async def pieces() -> AsyncIterator[bytes]:
        # Sent in pieces, a body declares no size: it is counted as it comes.
        body = json.dumps(update).encode()
        for start in range(0, len(body), 64 * 1024):
            yield body[start : start + 64 * 1024]

    undeclared = await client.post(
        WEBHOOK, content=pieces(), headers={SECRET_HEADER: WEBHOOK_SECRET}
    )
    assert undeclared.status_code == 413
    assert telegram_api.calls == []


async def test_a_call_cut_off_before_its_body_is_not_a_failure_of_the_site(
    client: AsyncClient, bot_keys: None, telegram_api: FakeTelegram
) -> None:
    # Telegram hangs up now and then: no «500» in the log and no error report for it.
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": WEBHOOK,
        "raw_path": WEBHOOK.encode(),
        "query_string": b"",
        "headers": [(SECRET_HEADER.lower().encode(), WEBHOOK_SECRET.encode())],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    await client._transport.app(scope, receive, send)  # type: ignore[attr-defined]
    assert sent[0]["status"] == 200
    assert telegram_api.calls == []


@pytest.mark.parametrize("failure", ["refused", "unreachable"])
async def test_when_the_answer_does_not_go_the_update_is_still_taken(
    client: AsyncClient,
    bot_keys: None,
    telegram_api: FakeTelegram,
    bot_log: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    # The person blocked the bot, or Telegram is out of reach: nothing to repeat the update
    # for, and the failure is in the log with Telegram's code.
    if failure == "refused":
        telegram_api.refusal = (403, "Forbidden: bot was blocked by the user")
    else:
        telegram_api.unreachable = True
    assert (await call(client)).status_code == 200
    assert len(telegram_api.calls) == 1
    [line] = [r.getMessage() for r in bot_log.records if r.name in BOT_LOGGERS]
    assert line == f"Telegram did not take sendMessage: {403 if failure == 'refused' else 0}"


# --- what stays out of the log --------------------------------------------------------------


async def test_who_wrote_and_what_never_reaches_the_log_nor_do_the_keys(
    client: AsyncClient,
    bot_keys: None,
    telegram_api: FakeTelegram,
    bot_log: pytest.LogCaptureFixture,
) -> None:
    text = "/start Мой паспорт MP1234567, телефон +375291234567"
    # Every way an update ends: answered, the answer refused, Telegram out of reach, left
    # unanswered, not an update at all, refused for its secret.
    assert (await call(client, telegram_message(text))).status_code == 200
    telegram_api.refusal = (403, "Forbidden: bot was blocked by the user")
    assert (await call(client, telegram_message(text))).status_code == 200
    telegram_api.refusal, telegram_api.unreachable = None, True
    assert (await call(client, telegram_message(text))).status_code == 200
    telegram_api.unreachable = False
    assert (await call(client, telegram_message(text[1:]))).status_code == 200
    assert (await call(client, text.encode())).status_code == 200
    assert (await call(client, telegram_message(text), secret="a-guess")).status_code == 403

    lines = [record.getMessage() for record in bot_log.records]
    logged = "\n".join([*lines, bot_log.text])
    # The failures were logged: the check below is not of an empty log.
    assert "Telegram did not take sendMessage: 403" in logged
    assert "Telegram did not take sendMessage: 0" in logged
    for private in (
        str(TELEGRAM_CHAT),
        *TELEGRAM_PERSON.values(),
        "MP1234567",
        "+375291234567",
        "паспорт",
        "blocked by the user",
        BOT_TOKEN,
        BOT_TOKEN.split(":")[1],
        WEBHOOK_SECRET,
        "a-guess",
    ):
        assert private not in logged, private

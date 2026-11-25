"""The client bot without HTTP (backlog 2.12.0): its switch and keys in the settings, what
it answers to an update (app/core/client_bot.py), how the site talks to Telegram's Bot API
(app/core/telegram.py) and where Telegram is told to call.

Telegram itself is never called: conftest replaces the one function that would.
"""

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core import telegram
from app.core.client_bot import GREETING, Reply, reply_to
from app.core.config import InvalidSettings, Settings, get_settings
from app.core.error_reports import scrub_breadcrumb
from app.core.telegram import MAX_ANSWER_BYTES, MAX_CALLS, TelegramFailed, webhook_address
from tests.helpers import (
    BOT_TOKEN,
    TELEGRAM_CHAT,
    WEBHOOK_SECRET,
    FakeTelegram,
    telegram_message,
)

SERVER_KEY = "test-only-server-key-0123456789abcdef"
KEYS = {"telegram_bot_token": BOT_TOKEN, "telegram_webhook_secret": WEBHOOK_SECRET}
SITE = "https://crm.example.com"
WEBHOOK = f"{SITE}/api/telegram/webhook"


# --- the switch and the keys ---------------------------------------------------------------


def test_the_bot_is_on_at_the_stands_and_off_in_production_until_switched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env, on in (("local", True), ("test", True), ("staging", True), ("production", False)):
        monkeypatch.delenv("CLIENT_BOT", raising=False)
        settings = Settings(env=env, secret_key=SERVER_KEY, **KEYS)
        assert settings.client_bot_enabled is on, env
    assert Settings(
        env="production", secret_key=SERVER_KEY, client_bot=True, **KEYS
    ).client_bot_enabled
    assert not Settings(
        env="staging", secret_key=SERVER_KEY, client_bot=False, **KEYS
    ).client_bot_enabled


@pytest.mark.parametrize("env", ["test", "staging", "production"])
@pytest.mark.parametrize("missing", [("telegram_bot_token",), ("telegram_webhook_secret",), KEYS])
def test_without_both_keys_the_bot_is_off_everywhere(env: str, missing: tuple[str, ...]) -> None:
    # A switch alone starts nothing: with one key there is no bot to answer or no way to
    # tell Telegram from anybody else.
    keys = {name: value for name, value in KEYS.items() if name not in missing}
    settings = Settings(env=env, secret_key=SERVER_KEY, client_bot=True, **keys)
    assert not settings.client_bot_enabled


@pytest.mark.parametrize(
    "secret",
    [
        "a secret with spaces in it, long enough",
        "секрет-не-латиницей-" * 2,
        "dots.are.not.what.telegram.takes.in.a.secret",
        "x" * 257,
    ],
)
def test_a_webhook_secret_telegram_would_not_take_stops_the_start(
    environment: Callable[..., None], secret: str
) -> None:
    # Telegram takes 1–256 characters of A–Z, a–z, 0–9, _ and -; with any other the webhook
    # could never be set, and the site would wait for calls that never pass.
    environment(ENV="test", TELEGRAM_WEBHOOK_SECRET=secret)
    with pytest.raises(InvalidSettings, match="TELEGRAM_WEBHOOK_SECRET") as refused:
        get_settings()
    assert secret not in str(refused.value)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_outside_development_a_short_webhook_secret_stops_the_start(
    environment: Callable[..., None], env: str
) -> None:
    # The secret is all that tells Telegram from anybody else: a guessable one is none.
    environment(ENV=env, TELEGRAM_WEBHOOK_SECRET="x" * 31)
    with pytest.raises(InvalidSettings, match="TELEGRAM_WEBHOOK_SECRET"):
        get_settings()
    environment(TELEGRAM_WEBHOOK_SECRET="x" * 32)
    assert get_settings().telegram_webhook_secret == "x" * 32
    # A developer's machine may keep a short one.
    environment(ENV="local", TELEGRAM_WEBHOOK_SECRET="dev")
    assert get_settings().telegram_webhook_secret == "dev"


@pytest.mark.parametrize(
    "token",
    [
        "123456789",  # the bot's number alone
        "bot123456789:TEST-not-a-real-bot-token-0123456789",  # pasted with the URL's prefix
        "123456789:too-short",
        "123456789:TEST-not-a-real-bot-token-0123456789/getMe",
        "123456789:TEST not a real bot token 0123456789",
    ],
)
def test_a_mistyped_bot_token_stops_the_start_without_quoting_it(
    environment: Callable[..., None], token: str
) -> None:
    # The token goes into the address of every call to Telegram: only its own shape may.
    environment(ENV="test", TELEGRAM_BOT_TOKEN=token)
    with pytest.raises(InvalidSettings, match="TELEGRAM_BOT_TOKEN") as refused:
        get_settings()
    assert token not in str(refused.value)


def test_the_keys_are_read_without_the_spaces_around_them(
    environment: Callable[..., None],
) -> None:
    environment(TELEGRAM_BOT_TOKEN=f" {BOT_TOKEN}\n", TELEGRAM_WEBHOOK_SECRET=f"{WEBHOOK_SECRET} ")
    settings = get_settings()
    assert settings.telegram_bot_token == BOT_TOKEN
    assert settings.telegram_webhook_secret == WEBHOOK_SECRET
    assert settings.client_bot_enabled


# --- what the bot answers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "greeting"),
    [(None, "ru"), ("ru", "ru"), ("be", "ru"), ("pl", "ru"), ("en", "en"), ("en-GB", "en")],
)
def test_start_in_a_private_chat_gets_the_greeting(language: str | None, greeting: str) -> None:
    # Russian unless the person's Telegram is in English.
    reply = reply_to(telegram_message("/start", language=language))
    assert reply == Reply(chat_id=TELEGRAM_CHAT, text=GREETING[greeting])


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/start c0ffee",  # opened by a link with a code: the code waits for 2.12.1
        "/start@VisaHelperBot",
        "/start@VisaHelperBot c0ffee",
        "/start\nand more",
    ],
)
def test_start_is_known_in_every_form_telegram_sends_it(text: str) -> None:
    assert reply_to(telegram_message(text)) == Reply(TELEGRAM_CHAT, GREETING["ru"])


@pytest.mark.parametrize(
    "text", ["start", "/started", "/startx 1", " /start", "Привет", "/help", ""]
)
def test_any_other_text_gets_no_answer(text: str) -> None:
    assert reply_to(telegram_message(text)) is None


@pytest.mark.parametrize("chat", ["group", "supergroup", "channel"])
def test_the_bot_answers_only_in_a_private_chat(chat: str) -> None:
    # In a group the greeting would be read by everyone in it.
    assert reply_to(telegram_message("/start", chat=chat)) is None


def _without(key: str) -> dict:
    update = telegram_message()
    del update["message"][key]
    return update


def _with_chat_id(value: object) -> dict:
    update = telegram_message()
    update["message"]["chat"]["id"] = value
    return update


@pytest.mark.parametrize(
    "update",
    [
        None,
        [],
        "text",
        {},
        {"update_id": 1},
        {"update_id": 1, "message": "/start"},
        {"update_id": 1, "edited_message": telegram_message()["message"]},
        {"update_id": 1, "callback_query": {"id": "1", "data": "/start"}},
        _without("chat"),
        _without("text"),  # a photo, a contact, a sticker
        _with_chat_id(str(TELEGRAM_CHAT)),
        _with_chat_id(True),
        _with_chat_id(None),
        {**telegram_message(), "message": {**telegram_message()["message"], "text": 7}},
        {**telegram_message(), "message": {**telegram_message()["message"], "chat": []}},
    ],
)
def test_anything_that_is_not_a_start_message_is_left_unanswered(update: object) -> None:
    assert reply_to(update) is None


def test_the_greeting_is_written_in_both_languages() -> None:
    assert set(GREETING) == {"ru", "en"}
    assert GREETING["ru"] != GREETING["en"]
    for text in GREETING.values():
        assert 20 < len(text) < 4096  # one Telegram message


# --- the calls to Telegram -----------------------------------------------------------------


async def test_a_message_goes_to_telegram_under_the_bots_token(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    message_id = await telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!")
    assert telegram_api.urls == [f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"]
    assert telegram_api.calls == [
        ("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": "Здравствуйте!"})
    ]
    # Telegram's number of the message: what a later edit names.
    assert message_id == 1


async def test_an_edit_names_the_message_it_changes(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    await telegram.edit_message(TELEGRAM_CHAT, 17, "Принято 3 из 5")
    assert telegram_api.calls == [
        ("editMessageText", {"chat_id": TELEGRAM_CHAT, "message_id": 17, "text": "Принято 3 из 5"})
    ]


async def test_an_edit_to_the_same_text_is_not_a_failure(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    # Telegram refuses an edit that changes nothing; the message already says it.
    telegram_api.refusal = (
        400,
        "Bad Request: message is not modified: specified new message content and reply "
        "markup are exactly the same as a current content and reply markup of the message",
    )
    await telegram.edit_message(TELEGRAM_CHAT, 17, "Принято 3 из 5")
    telegram_api.refusal = (400, "Bad Request: message to edit not found")
    with pytest.raises(TelegramFailed) as failed:
        await telegram.edit_message(TELEGRAM_CHAT, 17, "Принято 3 из 5")
    assert failed.value.code == 400


async def test_a_refusal_carries_telegrams_code_and_nothing_secret(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    telegram_api.refusal = (403, "Forbidden: bot was blocked by the user")
    with pytest.raises(TelegramFailed) as failed:
        await telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!")
    error = failed.value
    assert (error.method, error.code) == ("sendMessage", 403)
    assert error.description == "Forbidden: bot was blocked by the user"
    # What a log or an error report would print: the method and the code.
    assert str(error) == "sendMessage: 403"
    assert BOT_TOKEN not in repr(error) and str(TELEGRAM_CHAT) not in repr(error)


async def test_telegram_out_of_reach_is_a_failure_that_does_not_quote_the_address(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    telegram_api.unreachable = True
    with pytest.raises(TelegramFailed) as failed:
        await telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!")
    error = failed.value
    assert (error.method, error.code, error.description) == ("sendMessage", 0, "")
    # The network's own error names the address with the token in it: it is not kept, not
    # even as the cause a traceback or an error report would print.
    assert error.__cause__ is None and error.__context__ is None
    assert BOT_TOKEN not in repr(error)


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, b"<html>a proxy's page</html>", 0),
        (502, b"Bad Gateway", 502),
        (200, b"[]", 0),
        (200, b'{"ok": true}', 0),  # no message in the answer to sendMessage
        (200, b'{"ok": true, "result": {"message_id": "17"}}', 0),
        (401, b'{"ok": false, "description": "Unauthorized"}', 401),
        (200, b'{"ok": false, "error_code": "x"}', 0),
    ],
)
async def test_an_answer_that_is_not_telegrams_is_a_failure(
    bot_keys: None, monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, code: int
) -> None:
    monkeypatch.setattr(telegram, "_post", lambda url, sent: (status, body))
    with pytest.raises(TelegramFailed) as failed:
        await telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!")
    assert failed.value.code == code


async def test_without_a_token_telegram_is_not_asked(telegram_api: FakeTelegram) -> None:
    assert get_settings().telegram_bot_token == ""
    for call in (
        telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!"),
        telegram.edit_message(TELEGRAM_CHAT, 17, "Принято 3 из 5"),
        telegram.set_webhook(WEBHOOK, WEBHOOK_SECRET),
        telegram.delete_webhook(),
    ):
        with pytest.raises(TelegramFailed) as failed:
            await call
        assert failed.value.code == 0
    assert telegram_api.calls == []


@pytest.mark.parametrize(
    "variables",
    [{"CLIENT_BOT": "false"}, {"ENV": "production"}, {"TELEGRAM_WEBHOOK_SECRET": None}],
)
async def test_a_switched_off_bot_writes_to_nobody(
    bot_keys: None,
    environment: Callable[..., None],
    telegram_api: FakeTelegram,
    variables: dict[str, str | None],
) -> None:
    # The switch closes the messages themselves, not only the webhook: whatever part of the
    # site asks for one later (a reminder, a status), none goes.
    environment(**variables)
    for call in (
        telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!"),
        telegram.edit_message(TELEGRAM_CHAT, 17, "Принято 3 из 5"),
        # Nor is Telegram told to call an address that answers «not found».
        telegram.set_webhook(WEBHOOK, WEBHOOK_SECRET),
    ):
        with pytest.raises(TelegramFailed) as failed:
            await call
        assert failed.value.code == 0
    assert telegram_api.calls == []
    # Telegram can still be told to stop calling the site.
    await telegram.delete_webhook()
    assert [method for method, _ in telegram_api.calls] == ["deleteWebhook"]


async def test_calls_to_telegram_wait_in_a_few_threads_of_their_own(
    bot_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not in the threads the file storage, the mail and Word templates share: with Telegram
    # slow or out of reach, a crowd of people opening the bot must not hold those up.
    threads: list[str] = []

    def post(url: str, body: bytes) -> tuple[int, bytes]:
        threads.append(threading.current_thread().name)
        return 200, b'{"ok": true, "result": {"message_id": 1}}'

    monkeypatch.setattr(telegram, "_post", post)
    await asyncio.gather(
        *(telegram.send_message(TELEGRAM_CHAT, "Здравствуйте!") for _ in range(MAX_CALLS * 4))
    )
    assert len(threads) == MAX_CALLS * 4
    assert all(name.startswith("telegram") for name in threads)
    assert len(set(threads)) <= MAX_CALLS


async def test_telegram_is_told_to_call_over_as_few_connections(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    await telegram.set_webhook(WEBHOOK, WEBHOOK_SECRET)
    assert telegram_api.calls == [
        (
            "setWebhook",
            {
                "url": WEBHOOK,
                "secret_token": WEBHOOK_SECRET,
                # Only the updates the bot acts on, over no more connections than it has
                # threads to answer from.
                "allowed_updates": ["message"],
                "max_connections": MAX_CALLS,
            },
        )
    ]


def test_a_call_to_telegram_leaves_no_token_in_an_error_report() -> None:
    # The SDK notes outgoing requests with their address; of Telegram's only the host
    # leaves the server (app/core/error_reports.py).
    crumb = {
        "type": "http",
        "category": "httplib",
        "data": {
            "url": f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            "method": "POST",
            "status_code": 200,
        },
    }
    assert scrub_breadcrumb(crumb, {}) == {
        "type": "http",
        "category": "httplib",
        "data": {"url": "https://api.telegram.org", "method": "POST", "status_code": 200},
    }


# --- the webhook's address ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("site", "address"),
    [
        (SITE, WEBHOOK),
        (f"{SITE}/", WEBHOOK),
        (f"  {SITE}\n", WEBHOOK),
        ("https://CRM.Example.com", WEBHOOK),
        ("https://crm.example.com:8443", "https://crm.example.com:8443/api/telegram/webhook"),
        ("https://visa-crm.xn--90ais", "https://visa-crm.xn--90ais/api/telegram/webhook"),
    ],
)
def test_the_webhook_is_at_the_sites_own_address(site: str, address: str) -> None:
    assert webhook_address(site) == address


@pytest.mark.parametrize(
    "site",
    [
        "",
        "crm.example.com",
        "http://crm.example.com",  # Telegram calls only over HTTPS
        "https://",
        "https://crm.example.com/app",
        "https://crm.example.com/api/telegram/webhook",
        "https://crm.example.com?x=1",
        "https://crm.example.com#x",
        "https://user:password@crm.example.com",
        "https://crm.example.com:port",
        "https://crm.example.com:0",
        "https://crm.example.com:99999",
        # A name Telegram might read differently from this site, and call somebody else.
        "https://evil.example\\.crm.example.com",
        "https://crm.example.com%2f.evil.example",
        "https://crm.exam ple.com",
        "https://crm.example.com\t.evil.example",
        "https://crm.example.com\n.evil.example",
        "https://crm..example.com",
        "https://crm.exampıe.com",  # a dotless ı for the l
        "https://crm.examſle.com",  # a long ſ for the p
        "https://crm.example.Kom",  # the Kelvin sign for a K
        "https://-crm.example.com",
        # Not a site's name: Telegram calls a domain with a certificate.
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]:8443",
    ],
)
def test_only_the_address_of_a_site_over_https_is_taken(site: str) -> None:
    with pytest.raises(ValueError, match="https://"):
        webhook_address(site)


# --- the one function that goes out to the network ------------------------------------------

# conftest replaces it in every test; these few run the real one, against a server of their
# own on this machine.
REAL_POST = telegram._post


class _Api(BaseHTTPRequestHandler):
    """A stand-in for Telegram's server: reads the request whole, as a real server does,
    then answers as its `post` says."""

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        self.post(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def post(self, body: bytes) -> None:
        raise NotImplementedError

    def answer(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # The caller may have stopped reading (the answer is too long for it): not an error
        # of this server.
        with suppress(OSError):
            self.wfile.write(body)


@pytest.fixture
def local_api() -> Iterator[Callable[[type[_Api]], str]]:
    """Serve one handler on this machine; the address of sendMessage on it."""
    servers: list[ThreadingHTTPServer] = []

    def serve(handler: type[_Api]) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/bot{BOT_TOKEN}/sendMessage"

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_the_real_call_posts_json_and_returns_the_answer(local_api) -> None:
    heard: list[tuple[str, str, str | None, bytes]] = []

    class Api(_Api):
        def post(self, body: bytes) -> None:
            heard.append((self.command, self.path, self.headers["Content-Type"], body))
            self.answer(200, b'{"ok": true, "result": true}')

    sent = b'{"chat_id": 5550001234, "text": "x"}'
    assert REAL_POST(local_api(Api), sent) == (200, b'{"ok": true, "result": true}')
    assert heard == [("POST", f"/bot{BOT_TOKEN}/sendMessage", "application/json", sent)]


def test_the_real_call_returns_a_refusal_with_telegrams_words(local_api) -> None:
    refusal = b'{"ok": false, "error_code": 403, "description": "Forbidden"}'

    class Api(_Api):
        def post(self, body: bytes) -> None:
            self.answer(403, refusal)

    assert REAL_POST(local_api(Api), b"{}") == (403, refusal)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_the_real_call_follows_no_redirect(local_api, status: int) -> None:
    # The address carries the token, and a 307 would carry the message on as well: nothing
    # goes anywhere Telegram's answer points.
    heard: list[tuple[str, str]] = []

    class Api(_Api):
        def do_GET(self) -> None:
            heard.append((self.command, self.path))
            self.answer(200, b'{"ok": true, "result": true}')

        def post(self, body: bytes) -> None:
            heard.append((self.command, self.path))
            self.answer(status, location="/elsewhere")

    assert REAL_POST(local_api(Api), b"{}")[0] == status
    assert heard == [("POST", f"/bot{BOT_TOKEN}/sendMessage")]


def test_the_real_call_reads_no_more_than_a_small_answer(local_api) -> None:
    class Api(_Api):
        def post(self, body: bytes) -> None:
            self.answer(200, b"x" * (MAX_ANSWER_BYTES + 4096))

    status, answer = REAL_POST(local_api(Api), b"{}")
    assert (status, len(answer)) == (200, MAX_ANSWER_BYTES)


def test_the_real_call_does_not_wait_for_a_silent_telegram(
    local_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    over = threading.Event()

    class Api(_Api):
        def post(self, body: bytes) -> None:
            over.wait(10)  # says nothing until the test is over

    monkeypatch.setattr(telegram, "TIMEOUT", 0.2)
    try:
        with pytest.raises(OSError):
            REAL_POST(local_api(Api), b"{}")
    finally:
        over.set()

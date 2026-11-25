"""Telegram's Bot API for the client bot (backlog 2.12): sending a message, editing one in
place, setting and removing the webhook.

One bot serves the whole platform; its token is a Railway variable (TELEGRAM_BOT_TOKEN,
app/core/config.py). Telegram takes the token only as a part of the address, so the address
is a secret here: it is built in `_call` and handed to `_post`, the one function that goes
out to the network (tests replace it, conftest.py), and it never reaches a log line, an
error's text or the error a caller sees. A failure is a `TelegramFailed` with the method
and Telegram's code, nothing of who the message was for.
"""

import asyncio
import http.client
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import get_settings

API_URL = "https://api.telegram.org/bot{token}/{method}"
# The site's own address of the webhook (app/api/telegram.py), and what Telegram sends to
# it: only the kinds of update the bot acts on (app/core/client_bot.py).
WEBHOOK_PATH = "/api/telegram/webhook"
ALLOWED_UPDATES = ("message",)
# Telegram answers in well under a second. Each step of a call (connecting, the TLS
# handshake, every read) waits no longer than this many seconds; looking the host up is the
# system's and has its own limit.
TIMEOUT = 5
# Calls to Telegram wait in a few threads of their own: when Telegram is slow or out of
# reach they queue here, and the threads the rest of the site shares (the file storage, the
# mail, Word templates) stay free. Telegram is told to call the webhook over no more
# connections than this (`set_webhook`), so answers do not pile up either.
MAX_CALLS = 4
_threads = ThreadPoolExecutor(max_workers=MAX_CALLS, thread_name_prefix="telegram")
# A site's address as its owner types it: https, a domain name, a port if any. The name is
# Latin letters, digits, hyphens and dots only (re.ASCII: without it «ignore case» lets in
# look-alikes such as the dotless ı): anything cleverer Telegram might read differently from
# this site and call somebody else, with the webhook's secret in the call.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
SITE = re.compile(
    rf"https://((?:{_LABEL}\.)+[a-z](?:[a-z0-9-]{{0,61}}[a-z0-9])?)(?::([0-9]{{1,5}}))?/?",
    re.IGNORECASE | re.ASCII,
)
# An answer is a small JSON object: no more of it than this is read.
MAX_ANSWER_BYTES = 64 * 1024
# What Telegram says to an edit that changes nothing.
NOT_MODIFIED = "message is not modified"


class TelegramFailed(RuntimeError):
    """Telegram did not do what was asked. `code` is its error code (403: the person
    blocked the bot, 401: a wrong token, 429: too many messages) or 0 when it was not
    asked (the bot is switched off, no token), was not reached or its answer could not be
    read. `description` is Telegram's own text for the
    command line; it is not part of the error's text, so it does not get into a log."""

    def __init__(self, method: str, code: int, description: str = "") -> None:
        super().__init__(f"{method}: {code}")
        self.method = method
        self.code = code
        self.description = description


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """The Bot API does not redirect; were it to, the request is not sent on anywhere."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def _post(url: str, body: bytes) -> tuple[int, bytes]:
    """Send a JSON body to Telegram; its HTTP status and answer. The one place that goes
    out to the network. Raises OSError or http.client.HTTPException when it does not get
    through; their text may quote the address, which `_call` therefore drops."""
    request = urllib.request.Request(  # noqa: S310 — a fixed https host (API_URL)
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_ANSWER_BYTES)
    except urllib.error.HTTPError as refused:
        # Telegram explains a refusal in the body of a 4xx answer.
        with refused:
            return refused.code, refused.read(MAX_ANSWER_BYTES)


async def _call(method: str, **params: Any) -> Any:
    """Ask Telegram for `method`; the `result` of its answer."""
    token = get_settings().telegram_bot_token
    if not token:
        raise TelegramFailed(method, 0)
    url = API_URL.format(token=token, method=method)
    body = json.dumps(params).encode()
    try:
        loop = asyncio.get_running_loop()
        status, raw = await loop.run_in_executor(_threads, _post, url, body)
    except (OSError, http.client.HTTPException):
        status, raw = 0, b""
    # Raised here, outside the handler: the network's error names the address with the
    # token in it and must not travel with this one as its context.
    if not status:
        raise TelegramFailed(method, 0)
    try:
        answer = json.loads(raw)
    except (ValueError, RecursionError):
        answer = None
    if not isinstance(answer, dict):
        # Not Telegram's answer (a proxy's page): the HTTP status if it tells of a failure.
        raise TelegramFailed(method, status if status >= 400 else 0)
    if answer.get("ok") is not True:
        code = answer.get("error_code")
        if not isinstance(code, int) or isinstance(code, bool):
            code = status if status >= 400 else 0
        description = answer.get("description")
        raise TelegramFailed(method, code, description if isinstance(description, str) else "")
    return answer.get("result")


def _only_while_the_bot_is_on(method: str) -> None:
    # The switch closes everything (docs/process.md → «Флаг закрывает всё»): whoever the
    # caller is, a switched-off bot writes to nobody.
    if not get_settings().client_bot_enabled:
        raise TelegramFailed(method, 0)


async def send_message(chat_id: int, text: str) -> int:
    """Send a plain-text message to a chat; Telegram's number of the message, which a
    later `edit_message` names."""
    _only_while_the_bot_is_on("sendMessage")
    result = await _call("sendMessage", chat_id=chat_id, text=text)
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        raise TelegramFailed("sendMessage", 0)
    return message_id


async def edit_message(chat_id: int, message_id: int, text: str) -> None:
    """Replace the text of a message the bot sent. Editing to the same text is fine."""
    _only_while_the_bot_is_on("editMessageText")
    try:
        await _call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
    except TelegramFailed as failed:
        if failed.code == 400 and NOT_MODIFIED in failed.description:
            return
        raise


def webhook_address(site: str) -> str:
    """Where Telegram calls for a site at `site` (https://crm.example.com). Telegram calls
    only over HTTPS; anything but the site's own address is a typing mistake."""
    found = SITE.fullmatch(site.strip())
    port = found.group(2) if found else None
    if found is None or (port is not None and not 0 < int(port) < 65536):
        raise ValueError("the site's address is needed, like https://crm.example.com")
    return f"https://{found.group(1).lower()}{f':{int(port)}' if port else ''}{WEBHOOK_PATH}"


async def set_webhook(url: str, secret: str) -> None:
    """Tell Telegram to send the bot's updates to `url`, with `secret` in a header of
    every call (app/api/telegram.py checks it)."""
    _only_while_the_bot_is_on("setWebhook")
    await _call(
        "setWebhook",
        url=url,
        secret_token=secret,
        allowed_updates=list(ALLOWED_UPDATES),
        max_connections=MAX_CALLS,
    )


async def delete_webhook() -> None:
    """Tell Telegram to stop calling the site. Works for a switched-off bot too: that is
    when it is needed."""
    await _call("deleteWebhook")

"""The webhook of the client bot (backlog 2.12.0): Telegram calls it with every message
written to the bot. One bot serves the whole platform (app/core/client_bot.py).

Anybody on the internet can call this address, so it answers only Telegram: Telegram
repeats the secret the webhook was set with (TELEGRAM_WEBHOOK_SECRET, `python -m app.cli
telegram-webhook`) in a header of every call. While the bot is switched off (CLIENT_BOT) or
a key is missing, the address answers «not found» to everybody, Telegram included. The body
is limited like any form (app/core/request_limit.py) and read only after the secret is
checked.

An update names the person and carries what they wrote: nothing of it is logged, here or in
the modules this one calls. The log gets Telegram's method and error code when an answer
did not go.
"""

import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request, status
from starlette.requests import ClientDisconnect

from app.core import client_bot
from app.core.config import get_settings
from app.core.telegram import TelegramFailed

logger = logging.getLogger(__name__)

router = APIRouter()

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# Telegram reads nothing from the answer but its status.
TAKEN = {"ok": True}


@router.post("/telegram/webhook")
async def webhook(request: Request) -> dict[str, bool]:
    settings = get_settings()
    if not settings.client_bot_enabled:
        # FastAPI's own «not found»: a switched-off bot takes nothing and says nothing of
        # its own.
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Compared as bytes and in constant time: a header may carry any bytes, and the time
    # the comparison takes must not tell how much of a guess was right.
    sent = request.headers.get(SECRET_HEADER, "").encode("latin-1")
    if not hmac.compare_digest(sent, settings.telegram_webhook_secret.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    # From here on the caller is Telegram, and whatever the update turns out to be, the
    # answer is «taken»: Telegram repeats an update it got an error for, for hours, and a
    # repeat would change nothing. (A body over the limit is still refused, with 413.)
    try:
        update = json.loads(await request.body())
    except ClientDisconnect:
        # Telegram hung up before the body came: nothing to act on, nobody to answer.
        return TAKEN
    except (ValueError, RecursionError):
        # Not JSON, or nested deeper than the decoder goes: not an update.
        logger.warning("A call to the Telegram webhook was not an update: dropped")
        return TAKEN
    try:
        await client_bot.handle(update)
    except TelegramFailed as failed:
        # The person blocked the bot, or Telegram is out of reach. The method and the
        # code only: the error's description may name the chat.
        logger.warning("Telegram did not take %s: %d", failed.method, failed.code)
    return TAKEN

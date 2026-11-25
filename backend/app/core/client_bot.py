"""The client bot in Telegram (backlog 2.12): what it says to what people write to it.

So far the bot only greets whoever opens it (2.12.0). It looks nobody up: finding the
client's card by the phone number or the nick, the links to sign and to upload, the status
message and the reminders come with the next items (2.12.1 on), and the greeting will
change with them.

An update is whatever Telegram sent to the webhook (app/api/telegram.py), already decoded:
it is read here field by field and trusted in nothing. It names the person (the name, the
nick, the number of the Telegram account) and carries what they wrote, so nothing of it is
logged.
"""

import re
from dataclasses import dataclass

from app.core import telegram

# The command Telegram sends when a person opens the bot: alone, with the code of the link
# that brought them («/start c0ffee») or addressed to the bot by name («/start@SomeBot»).
START = re.compile(r"/start(?:@\w+)?(?:\s.*)?", re.DOTALL)

# Russian unless the person's Telegram is in English.
GREETING = {
    "ru": (
        "Здравствуйте! Это бот вашего визового специалиста. Через него специалист будет "
        "присылать вам ссылки, чтобы подписать договор и загрузить документы.\n\n"
        "Файлы и документы в этот чат отправлять не нужно: бот их не принимает."
    ),
    "en": (
        "Hello! This is your visa specialist's bot. Your specialist will use it to send you "
        "links to sign the contract and upload your documents.\n\n"
        "Please don't send files or documents to this chat: the bot doesn't accept them."
    ),
}


@dataclass(frozen=True)
class Reply:
    """A message the bot sends: to which chat and what."""

    chat_id: int
    text: str


def _language(sender: object) -> str:
    code = sender.get("language_code") if isinstance(sender, dict) else None
    return "en" if isinstance(code, str) and code.lower().startswith("en") else "ru"


def reply_to(update: object) -> Reply | None:
    """What the bot answers to an update, or None for one it leaves unanswered: anything
    but /start, written anywhere but in a private chat (in a group everyone would read the
    answer), or not shaped as Telegram shapes an update."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return None
    chat, text = message.get("chat"), message.get("text")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    if not isinstance(text, str) or not START.fullmatch(text):
        return None
    return Reply(chat_id, GREETING[_language(message.get("from"))])


async def handle(update: object) -> None:
    """Act on one update from Telegram. Raises `telegram.TelegramFailed` when the answer
    did not go."""
    reply = reply_to(update)
    if reply is not None:
        await telegram.send_message(reply.chat_id, reply.text)

"""The command that tells Telegram where to call (backlog 2.12.0):
`python -m app.cli telegram-webhook set https://<site>` and `… remove`.

It sets the webhook only for a bot that is switched on and has both keys, repeats neither
key in what it prints, and removes the webhook whatever the switch says. Telegram itself is
never called: conftest replaces the one function that would.
"""

from collections.abc import Callable

import pytest

from app.cli import remove_telegram_webhook, set_telegram_webhook
from tests.support.helpers import BOT_TOKEN, WEBHOOK_SECRET, FakeTelegram

SITE = "https://crm.example.com"
WEBHOOK = f"{SITE}/api/telegram/webhook"


async def test_the_command_sets_the_webhook_with_its_secret(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    said = await set_telegram_webhook(SITE)
    assert telegram_api.calls == [
        (
            "setWebhook",
            {
                "url": WEBHOOK,
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message"],
                "max_connections": 4,
            },
        )
    ]
    # It says where Telegram will call, and neither key.
    assert WEBHOOK in said
    assert WEBHOOK_SECRET not in said and BOT_TOKEN not in said


@pytest.mark.parametrize(
    "variables",
    [
        {"TELEGRAM_BOT_TOKEN": None},
        {"TELEGRAM_WEBHOOK_SECRET": None},
        {"CLIENT_BOT": "false"},
        {"ENV": "production"},  # off there until the owner switches it on
    ],
)
async def test_the_command_sets_no_webhook_while_the_bot_is_off(
    bot_keys: None,
    environment: Callable[..., None],
    telegram_api: FakeTelegram,
    variables: dict[str, str | None],
) -> None:
    # Telegram would call an address that answers «not found».
    environment(**variables)
    with pytest.raises(SystemExit) as refused:
        await set_telegram_webhook(SITE)
    assert "CLIENT_BOT" in str(refused.value)
    assert telegram_api.calls == []


async def test_the_command_refuses_an_address_telegram_cannot_call(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    with pytest.raises(SystemExit) as refused:
        await set_telegram_webhook("http://crm.example.com")
    assert "https://" in str(refused.value)
    assert telegram_api.calls == []


async def test_the_command_reports_telegrams_refusal_without_the_keys(
    bot_keys: None, telegram_api: FakeTelegram
) -> None:
    telegram_api.refusal = (401, "Unauthorized")
    for command in (set_telegram_webhook(SITE), remove_telegram_webhook()):
        with pytest.raises(SystemExit) as refused:
            await command
        said = str(refused.value)
        assert "401" in said and "Unauthorized" in said
        assert WEBHOOK_SECRET not in said and BOT_TOKEN not in said


async def test_the_command_removes_the_webhook_even_when_the_bot_is_off(
    bot_keys: None, environment: Callable[..., None], telegram_api: FakeTelegram
) -> None:
    # Switching the bot off must not leave Telegram calling the site.
    environment(CLIENT_BOT="false", TELEGRAM_WEBHOOK_SECRET=None)
    said = await remove_telegram_webhook()
    assert telegram_api.calls == [("deleteWebhook", {})]
    assert BOT_TOKEN not in said


async def test_without_a_token_the_command_has_no_bot_to_ask(
    telegram_api: FakeTelegram,
) -> None:
    with pytest.raises(SystemExit) as refused:
        await remove_telegram_webhook()
    assert "TELEGRAM_BOT_TOKEN" in str(refused.value)
    assert telegram_api.calls == []

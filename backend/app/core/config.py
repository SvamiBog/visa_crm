import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sentry_sdk.utils import BadDsn, Dsn

Environment = Literal["local", "test", "staging", "production"]
# A developer's machine and the tests: plain HTTP, files in memory, mail kept instead of
# sent, API docs.
DEVELOPMENT: tuple[Environment, ...] = ("local", "test")
# Outside development the server key must be a real secret: an empty, known or short one is
# as good as none (audit h03.4, h14.3).
MIN_SECRET_KEY_LENGTH = 32
# Sentry's EU data region: its DSNs point at o<N>.ingest.de.sentry.io.
SENTRY_EU_HOST = ".de.sentry.io"
# A bot's token as @BotFather gives it: the bot's number, a colon and the key.
TELEGRAM_BOT_TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{30,64}")
# What Telegram takes as the secret of a webhook (setWebhook → secret_token).
TELEGRAM_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{1,256}")


class InvalidSettings(RuntimeError):
    """The environment is not one the site may run in: the server does not start."""


class Settings(BaseSettings):
    """Runtime settings. Values come from environment variables (Railway) or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Unset means the real site: a forgotten variable gives the strictest mode, not a
    # developer's machine (audit h14.1, owner's decision of backlog S19). Read in any case
    # and without spaces; any other value stops the start.
    env: Environment = "production"
    # postgresql+asyncpg://user:pass@host:5432/db
    # A plain postgresql:// URL from Railway is converted in async_database_url.
    database_url: str = "postgresql+asyncpg://visacrm:visacrm@localhost:5432/visacrm"
    secret_key: str = "change-me"
    # 32 random bytes, base64. Encrypts the tenants' data keys and TOTP secrets.
    vault_master_key: str = ""
    # Set only while rotating the master key (docs/operations.md): old values still decrypt.
    vault_master_key_previous: str = ""
    frontend_dist: Path = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    # Outgoing mail (app/core/mail.py). Locally and on staging: Mailpit (host, port 1025,
    # SMTP_TLS=none). In production: the mail provider's SMTP.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: Literal["starttls", "ssl", "none"] = "starttls"
    mail_from: str = "VisaCRM <no-reply@localhost>"
    # File storage (app/core/storage.py): any S3-compatible service in the EU, files are
    # encrypted before upload. Without S3_BUCKET, local development and tests keep files in
    # memory and other environments refuse uploads.
    s3_endpoint: str = ""
    s3_region: str = "auto"
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    # The staging stand only (docs/staging.md; SEED_DEMO=1 in start.sh): every demo account
    # gets this password and authenticator secret.
    demo_password: str = ""
    demo_totp_secret: str = ""
    # Error reports to Sentry (app/core/error_reports.py, docs/deploy.md): the DSN of the
    # project. Unset: the SDK does not start and nothing is sent anywhere.
    sentry_dsn: str = ""
    # Railway sets it on a deploy from GitHub: the release an error report belongs to.
    railway_git_commit_sha: str = ""
    # The client portal (backlog 2.9). Unset: on at the stands (local, test, staging, PR
    # stands), off in production until the owner turns it on with CLIENT_PORTAL=true
    # (owner's decision 2026-11-24, docs/decisions.md → «Портал клиента и вход по коду»).
    client_portal: bool | None = None
    # Removing passport data after the decision (backlog 2.8, app/core/passport_purge.py):
    # always on the stands (local, test, staging, PR stands); in production off until this
    # day, and what is already due then waits 14 days more (owner's decision 2026-11-24,
    # docs/decisions.md). Set it to the day it is switched on, never to a day long past.
    passport_purge_from: date | None = None
    # The client bot in Telegram (backlog 2.12, app/api/telegram.py): one bot for the whole
    # platform. Unset: on at the stands, off in production until the owner turns it on with
    # CLIENT_BOT=true, like the client portal. It works only with both keys below, which
    # are Railway variables and nothing else (docs/deploy.md).
    client_bot: bool | None = None
    # The bot's token from @BotFather: it is part of the address of every call to Telegram.
    telegram_bot_token: str = ""
    # Telegram repeats it in a header of every call to the webhook, which is how the site
    # tells Telegram from anybody else. Generate: python -c "import secrets;
    # print(secrets.token_urlsafe(48))"
    telegram_webhook_secret: str = ""

    @field_validator("env", mode="before")
    @classmethod
    def _env_in_any_case(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("sentry_dsn")
    @classmethod
    def _sentry_dsn_is_a_dsn(cls, value: str) -> str:
        # A mistyped DSN stops the start like any other setting: the reports would be lost
        # without a word. The reason only, never the value.
        value = value.strip()
        if value:
            try:
                host = Dsn(value).host
            except (BadDsn, ValueError):
                raise ValueError("not a Sentry DSN (https://KEY@HOST/PROJECT)") from None
            # Reports are kept in Sentry's EU region (docs/decisions.md): a DSN of another
            # region, pasted by mistake, would send them out of the EU.
            if not host.endswith(SENTRY_EU_HOST):
                raise ValueError(f"not a DSN of Sentry's EU region (*{SENTRY_EU_HOST})")
        return value

    @field_validator("telegram_bot_token")
    @classmethod
    def _bot_token_is_a_token(cls, value: str) -> str:
        # The token is put into the address of a request: only its own shape may be. The
        # reason only, never the value, as for every key here.
        value = value.strip()
        if value and not TELEGRAM_BOT_TOKEN.fullmatch(value):
            raise ValueError("not a bot token (NUMBER:KEY, as @BotFather gives it)")
        return value

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _webhook_secret_is_one_telegram_takes(cls, value: str) -> str:
        value = value.strip()
        if value and not TELEGRAM_WEBHOOK_SECRET.fullmatch(value):
            raise ValueError("must be 1-256 characters of A-Z, a-z, 0-9, _ and -")
        return value

    @model_validator(mode="after")
    def _webhook_secret_outside_development(self) -> Self:
        secret = self.telegram_webhook_secret
        if secret and not self.development and len(secret) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"TELEGRAM_WEBHOOK_SECRET must be at least {MIN_SECRET_KEY_LENGTH} random "
                f"characters outside ENV={'|'.join(DEVELOPMENT)}"
            )
        return self

    @model_validator(mode="after")
    def _server_key_outside_development(self) -> Self:
        if not self.development and len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} random characters "
                f"outside ENV={'|'.join(DEVELOPMENT)}"
            )
        return self

    @property
    def development(self) -> bool:
        return self.env in DEVELOPMENT

    @property
    def client_portal_enabled(self) -> bool:
        if self.client_portal is not None:
            return self.client_portal
        return self.env != "production"

    @property
    def client_bot_enabled(self) -> bool:
        """The switch and both keys: without any of the three there is no bot at all."""
        if not (self.telegram_bot_token and self.telegram_webhook_secret):
            return False
        if self.client_bot is not None:
            return self.client_bot
        return self.env != "production"

    @property
    def cookie_secure(self) -> bool:
        """Session cookie only over HTTPS everywhere except local development and tests."""
        return not self.development

    @property
    def api_docs(self) -> bool:
        """Swagger UI and the OpenAPI schema on a developer's machine only: the stands are
        on the internet like the site, and Swagger UI loads its scripts from a CDN without
        integrity checks (audit h15.2, docs/decisions.md)."""
        return self.development

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        return url


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        # Which setting and why, never its value: a server's start log must not carry its
        # secrets, so the validation error that quotes them is not chained either.
        reasons = []
        for e in error.errors(include_url=False, include_input=False):
            reason = e["msg"].removeprefix("Value error, ")
            field = ".".join(map(str, e["loc"])).upper()
            reasons.append(f"{field}: {reason}" if field else reason)
        raise InvalidSettings("; ".join(reasons)) from None

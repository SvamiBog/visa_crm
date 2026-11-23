from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
# A developer's machine and the tests: plain HTTP, files in memory, mail kept instead of
# sent, API docs.
DEVELOPMENT: tuple[Environment, ...] = ("local", "test")
# Outside development the server key must be a real secret: an empty, known or short one is
# as good as none (audit h03.4, h14.3).
MIN_SECRET_KEY_LENGTH = 32


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

    @field_validator("env", mode="before")
    @classmethod
    def _env_in_any_case(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

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

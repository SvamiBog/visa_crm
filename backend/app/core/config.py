from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values come from environment variables (Railway) or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
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

    @property
    def cookie_secure(self) -> bool:
        """Session cookie only over HTTPS everywhere except local development and tests."""
        return self.env not in ("local", "test")

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
    return Settings()

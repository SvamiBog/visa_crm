from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values come from environment variables (Railway) or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    # postgresql+asyncpg://user:pass@host:5432/db
    # A plain postgresql:// URL from Railway is converted in async_database_url.
    database_url: str = "postgresql+asyncpg://visacrm:visacrm@localhost:5432/visacrm"
    secret_key: str = "change-me"
    vault_master_key: str = ""
    frontend_dist: Path = Path(__file__).resolve().parents[3] / "frontend" / "dist"

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

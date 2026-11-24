"""The site does not run half-configured (backlog S19; audit h14.1, h14.2, h01.5, h03.6,
h03.4, h14.3).

A forgotten ENV gives the strictest mode, not a developer's machine: Secure cookies and
HSTS, no API docs, no files in memory, no mail codes in the log. A misspelt ENV or a missing
server key stops the start instead.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.core.config import InvalidSettings, Settings, get_settings
from app.main import create_app

# A server key of the right length; test-only, like the master key in conftest.py.
SERVER_KEY = "test-only-server-key-0123456789abcdef"


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., Settings]]:
    """Read the settings as a server starts with these variables; None leaves one unset.
    Runs in an empty folder, so a developer's backend/.env sets nothing here."""
    monkeypatch.chdir(tmp_path)

    def start(**variables: str | None) -> Settings:
        for name, value in variables.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return get_settings()

    yield start
    get_settings.cache_clear()


def test_a_forgotten_env_runs_in_the_strictest_mode(started) -> None:
    settings = started(ENV=None, SECRET_KEY=SERVER_KEY)
    assert settings.env == "production"
    assert settings.cookie_secure  # Secure cookie and HSTS
    assert not settings.api_docs


@pytest.mark.parametrize(
    ("value", "env"),
    [("Production", "production"), (" staging ", "staging"), ("LOCAL\n", "local")],
)
def test_env_is_read_whatever_its_case_and_spaces(started, value: str, env: str) -> None:
    settings = started(ENV=value, SECRET_KEY=SERVER_KEY)
    assert settings.env == env
    # The spelling does not decide the mode: «Production» is production.
    assert settings.cookie_secure is (env != "local")
    assert settings.api_docs is (env == "local")


@pytest.mark.parametrize("value", ["prod", "stage", "development", ""])
def test_an_unknown_env_stops_the_start(started, value: str) -> None:
    with pytest.raises(InvalidSettings, match="ENV"):
        started(ENV=value, SECRET_KEY=SERVER_KEY)


@pytest.mark.parametrize("env", ["staging", "production", None])
@pytest.mark.parametrize("key", [None, "", "change-me", "x" * 31])
def test_outside_development_a_strong_server_key_is_required(
    started, env: str | None, key: str | None
) -> None:
    with pytest.raises(InvalidSettings, match="SECRET_KEY"):
        started(ENV=env, SECRET_KEY=key)
    assert started(ENV=env, SECRET_KEY="x" * 32).secret_key == "x" * 32


@pytest.mark.parametrize("env", ["local", "test"])
def test_a_developers_machine_needs_no_server_key(started, env: str) -> None:
    # backend/.env.example keeps SECRET_KEY=change-me.
    assert started(ENV=env, SECRET_KEY="change-me").env == env
    assert started(ENV=env, SECRET_KEY=None).env == env


def test_the_refusal_names_the_setting_not_its_value(started) -> None:
    # The start log of a server must not carry its secrets, even wrong ones.
    with pytest.raises(InvalidSettings) as refused:
        started(
            ENV="production",
            SECRET_KEY="a-short-secret",
            DATABASE_URL="postgresql://visacrm:db-password-123@db.internal:5432/visacrm",
        )
    message = str(refused.value)
    assert "SECRET_KEY" in message
    assert "a-short-secret" not in message and "db-password-123" not in message
    # Nor does the traceback quote the validation error with the input values in it.
    assert refused.value.__suppress_context__


def test_the_app_does_not_start_half_configured(started) -> None:
    with pytest.raises(InvalidSettings):
        started(ENV="staging", SECRET_KEY=None)
    # uvicorn imports app.main, which builds the app: the import fails, the server exits.
    with pytest.raises(InvalidSettings, match="SECRET_KEY"):
        create_app()


@pytest.mark.parametrize(
    "dsn",
    [
        "0123456789abcdef@o1.ingest.de.sentry.io/2",  # no scheme
        "https://o1.ingest.de.sentry.io/2",  # no key
        "https://0123456789abcdef@o1.ingest.de.sentry.io/project",  # no project number
        "ftp://0123456789abcdef@o1.ingest.de.sentry.io/2",
        # Outside Sentry's EU region (docs/decisions.md): the reports stay in the EU.
        "https://0123456789abcdef@o1.ingest.us.sentry.io/2",
        "https://0123456789abcdef@o1.ingest.sentry.io/2",
        "https://0123456789abcdef@de.sentry.io.example.com/2",
    ],
)
def test_a_mistyped_sentry_dsn_stops_the_start_without_quoting_it(started, dsn: str) -> None:
    # Error reports lost without a word are worse than a deploy that does not come up.
    with pytest.raises(InvalidSettings, match="SENTRY_DSN") as refused:
        started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=dsn)
    assert "0123456789abcdef" not in str(refused.value)


@pytest.mark.parametrize("dsn", [None, "", "  "])
def test_no_sentry_dsn_means_no_error_reports(started, dsn: str | None) -> None:
    assert started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=dsn).sentry_dsn == ""


def test_a_sentry_dsn_is_read_without_its_spaces(started) -> None:
    dsn = "https://0123456789abcdef@o1.ingest.de.sentry.io/2"
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=f" {dsn}\n")
    assert settings.sentry_dsn == dsn

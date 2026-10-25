import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import clock, db, rates, storage, two_factor
from app.core.config import get_settings
from app.core.modules import DEFAULT_MODULES
from app.core.security import hash_password
from app.core.two_factor import seal_secret
from app.main import create_app
from tests.helpers import CLOCK, PASSWORD, TOTP_SECRET, WALL_CLOCK, MakeUser

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://visacrm:visacrm@localhost:5432/visacrm_test"
)


# Test-only master key; real keys live in Railway variables and the local .env.
TEST_VAULT_MASTER_KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture(scope="session", autouse=True)
def vault_master_key() -> None:
    os.environ["VAULT_MASTER_KEY"] = TEST_VAULT_MASTER_KEY
    os.environ.pop("VAULT_MASTER_KEY_PREVIOUS", None)
    get_settings.cache_clear()


async def _recreate_database(url: str) -> None:
    target = make_url(url)
    server = target.set(drivername="postgresql", database="postgres")
    conn = await asyncpg.connect(server.render_as_string(hide_password=False))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{target.database}"')
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """TOTP checks use the test clock: helpers.login moves it one step per login."""
    monkeypatch.setattr(two_factor, "_now", CLOCK.time)


@pytest.fixture(autouse=True)
def stopped_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """«Today» stands still for the length of a test, the same for the app and the test
    (helpers.workspace_today): a run that crosses midnight in Minsk changes nothing."""
    WALL_CLOCK.at = clock._now()
    monkeypatch.setattr(clock, "_now", WALL_CLOCK.now)
    yield
    WALL_CLOCK.at = None


# BYN for one unit, every day: tests never call the National Bank.
FAKE_RATES = {"EUR": "3.400000", "USD": "3.100000", "PLN": "0.800000"}


@pytest.fixture(autouse=True)
def fake_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(currency: str, day) -> Decimal:
        if currency not in FAKE_RATES:
            raise rates.RateUnavailable(currency)
        return Decimal(FAKE_RATES[currency])

    monkeypatch.setattr(rates, "_fetch", fetch)
    monkeypatch.setattr(rates, "_misses", {})


@pytest.fixture(autouse=True)
def empty_file_storage() -> None:
    """Tests keep files in memory (no S3_BUCKET): each test starts with none."""
    storage.MEMORY.clear()


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch):
    """Set the master key (and optionally the previous one) for one test."""

    def set_keys(current: str, previous: str = "") -> None:
        monkeypatch.setenv("VAULT_MASTER_KEY", current)
        monkeypatch.setenv("VAULT_MASTER_KEY_PREVIOUS", previous)
        get_settings.cache_clear()

    yield set_keys
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Fresh test database, migrated up, down and up again (catches broken downgrades)."""
    asyncio.run(_recreate_database(TEST_DATABASE_URL))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)  # models and migrations describe the same schema
    asyncio.run(_snapshot_reference(TEST_DATABASE_URL))
    return TEST_DATABASE_URL


# Everything a test may write in workspaces; emptied after each test.
WORKSPACE_TABLES = (
    "audit_events",
    "employee_cases",
    "import_rows",
    "import_runs",
    "import_mappings",
    "user_recovery_codes",
    "email_changes",
    "user_sessions",
    "waiting_list_entries",
    "application_attempts",
    "vault_entries",
    "vault_systems",
    "package_items",
    "bookings",
    "reminder_dismissals",
    "visa_center_notes",
    "registrar_payments",
    "case_payments",
    "case_expenses",
    "case_finances",
    "exchange_rates",
    "visa_cases",
    "submission_groups",
    "consultations",
    "trips",
    "own_checklist_items",
    "contact_templates",
    "document_templates",
    "contact_documents",
    "contact_relations",
    "contact_notes",
    "contacts",
    "partner_terms",
    "registrar_prices",
    "prepayments",
    "partners",
    "registrars",
    "visa_purposes",
    "lead_channels",
    "users",
    "tenants",
)

# Shared reference data seeded by migrations, parents first. Tests may change it, so it is
# restored from a snapshot after each test.
REFERENCE_TABLES = (
    "countries",
    "vac_operators",
    "country_rules",
    "visa_centers",
    "checklist_items",
)


async def _snapshot_reference(url: str) -> None:
    conn = await asyncpg.connect(make_url(url).set(drivername="postgresql").render_as_string(False))
    try:
        await conn.execute("CREATE SCHEMA test_snapshot")
        for table in REFERENCE_TABLES:
            await conn.execute(f"CREATE TABLE test_snapshot.{table} AS TABLE public.{table}")
    finally:
        await conn.close()


@pytest.fixture
async def owner_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """Engine logged in as the database owner: test setup and cleanup, bypasses RLS."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    yield engine
    async with engine.begin() as conn:
        # Replica mode skips the audit log's append-only trigger: cleanup between tests only.
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(text(f"TRUNCATE {', '.join(WORKSPACE_TABLES)}"))
        # CASCADE: workspace tables refer to countries; they are already empty.
        await conn.execute(text(f"TRUNCATE {', '.join(REFERENCE_TABLES)} CASCADE"))
        for table in REFERENCE_TABLES:
            await conn.execute(text(f"INSERT INTO {table} SELECT * FROM test_snapshot.{table}"))
    await engine.dispose()


@pytest.fixture
def sessionmaker(owner_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """App sessions: same connection credentials, RLS applied by the session hook."""
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture
async def client(owner_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """HTTP client for the app. The app engine is rebuilt per test: each test has its own loop."""
    db.get_sessionmaker.cache_clear()
    db.get_engine.cache_clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    await db.get_engine().dispose()
    db.get_sessionmaker.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def make_user(owner_engine: AsyncEngine) -> MakeUser:
    """Create a user (and a tenant for non-admins). Returns the tenant id.

    Workspaces get the default (core) modules, like ones the admin creates.
    Admins and specialists get two-factor login on with the test secret (helpers.login
    passes the second step), unless they still have a temporary password.
    """

    async def make(
        email: str,
        role: str = "specialist",
        *,
        must_change_password: bool = False,
        tenant_id: uuid.UUID | None = None,
        two_factor: bool | None = None,
    ) -> uuid.UUID | None:
        if two_factor is None:
            two_factor = role != "employee" and not must_change_password
        user_id = uuid.uuid4()
        async with owner_engine.begin() as conn:
            if role != "admin" and tenant_id is None:
                tenant_id = uuid.uuid4()
                await conn.execute(
                    text("INSERT INTO tenants (id, name, modules) VALUES (:id, :name, :modules)"),
                    {"id": tenant_id, "name": f"Workspace of {email}", "modules": DEFAULT_MODULES},
                )
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, password_hash, role, "
                    "must_change_password, totp_secret_encrypted, totp_enabled_at) "
                    "VALUES (:id, :t, :e, :h, :r, :m, :secret, "
                    "CASE WHEN :on THEN now() END)"
                ),
                {
                    "id": user_id,
                    "secret": seal_secret(user_id, TOTP_SECRET) if two_factor else None,
                    "on": two_factor,
                    "t": tenant_id,
                    "e": email,
                    "h": hash_password(PASSWORD),
                    "r": role,
                    "m": must_change_password,
                },
            )
        return tenant_id

    return make

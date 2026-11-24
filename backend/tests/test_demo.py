"""Demo data for the stands: it fills empty workspaces, works through the API like real
data, runs only where the stand sets ENV, and gives a stand without DEMO_PASSWORD its own
random password (docs/staging.md)."""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_demo
from app.core.config import get_settings
from app.core.db import scope_to_system
from app.core.demo import (
    ADMIN_EMAIL,
    ALLOWED_ENVS,
    PASSPORTS_TO_REMOVE,
    DemoRefused,
    DemoSettings,
    _user,
    already_seeded,
    check_settings,
    seed,
    specialist_email,
)
from app.core.security import verify_password
from app.models import User, UserRole
from tests.helpers import TOTP_SECRET, login

PASSWORD = "demo-password-42"


@pytest.fixture
def stand_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., str]]:
    """Set ENV as a stand does, or leave it unset (None); returns the value the app sees.
    Runs in an empty folder, so a developer's backend/.env does not set ENV here."""
    monkeypatch.chdir(tmp_path)

    def set_env(env: str | None) -> str:
        if env is None:
            monkeypatch.delenv("ENV", raising=False)
        else:
            monkeypatch.setenv("ENV", env)
        get_settings.cache_clear()
        return get_settings().env

    yield set_env
    get_settings.cache_clear()


@pytest.fixture
def demo_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """What the demo seeding writes to the log. The migrations of the test database run in
    this process, and Alembic's logging config switches off the loggers made before them."""
    monkeypatch.setattr(logging.getLogger("app.core.demo"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="app.core.demo")
    return caplog


def _logged_passwords(log: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage().rsplit(" ", 1)[-1]
        for record in log.records
        if record.name == "app.core.demo"
    ]


def test_demo_data_is_refused_outside_the_stand(stand_env: Callable[..., str]):
    stand_env("production")
    with pytest.raises(DemoRefused):
        check_settings("production", PASSWORD, TOTP_SECRET)
    stand_env("staging")
    with pytest.raises(DemoRefused):
        check_settings("staging", "short", TOTP_SECRET)
    with pytest.raises(DemoRefused):
        check_settings("staging", PASSWORD, "not base32!")
    check_settings("staging", PASSWORD, TOTP_SECRET)
    check_settings("staging", PASSWORD, "")  # no secret: demo accounts without the code
    check_settings("staging", "", "")  # no DEMO_PASSWORD: the stand makes up its own


async def test_demo_data_is_refused_when_env_is_left_to_the_default(
    stand_env: Callable[..., str],
):
    # A server that forgot ENV runs with the default of config.py: no demo accounts there.
    env = stand_env(None)
    with pytest.raises(DemoRefused, match="ENV is not set"):
        check_settings(env, PASSWORD, "")
    # The command start.sh runs refuses before it touches the database.
    with pytest.raises(DemoRefused, match="ENV is not set"):
        await seed_demo(specialists=1, clients=10)


def test_demo_data_runs_where_the_stand_sets_env(stand_env: Callable[..., str], tmp_path: Path):
    for env in ALLOWED_ENVS:
        check_settings(stand_env(env), PASSWORD, "")
    # A developer's local stand sets it in backend/.env.
    (tmp_path / ".env").write_text("ENV=local\n", encoding="utf-8")
    check_settings(stand_env(None), PASSWORD, "")


async def test_a_stand_without_demo_password_gets_its_own_random_one(
    sessionmaker: async_sessionmaker[AsyncSession], demo_log: pytest.LogCaptureFixture
):
    passwords = []
    for _ in range(2):
        demo_log.clear()
        async with sessionmaker() as session:
            scope_to_system(session)
            await seed(session, DemoSettings("", specialists=0))
            logged = _logged_passwords(demo_log)
            assert len(logged) == 1, "the stand's password is shown once, in its log"
            admin = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
            assert admin is not None and verify_password(admin.password_hash, logged[0])
            await session.rollback()  # the next round is another, empty stand
        passwords.append(logged[0])
    assert passwords[0] != passwords[1]
    assert all(len(p) >= 16 for p in passwords)


async def test_a_set_demo_password_is_used_and_kept_out_of_the_log(
    sessionmaker: async_sessionmaker[AsyncSession], demo_log: pytest.LogCaptureFixture
):
    async with sessionmaker() as session:
        scope_to_system(session)
        await seed(session, DemoSettings(PASSWORD, specialists=0))
        admin = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None and verify_password(admin.password_hash, PASSWORD)
        await session.rollback()
    assert not _logged_passwords(demo_log) and PASSWORD not in demo_log.text


def test_demo_accounts_get_two_factor_only_with_a_secret():
    now = datetime.now(UTC)
    plain = _user("visa1@x.test", UserRole.SPECIALIST, DemoSettings(PASSWORD), uuid4(), now)
    assert plain.totp_enabled_at is None and plain.totp_secret_encrypted is None
    coded = _user(
        "visa1@x.test", UserRole.SPECIALIST, DemoSettings(PASSWORD, TOTP_SECRET), uuid4(), now
    )
    assert coded.totp_enabled_at is not None


async def test_demo_workspaces_work_like_real_ones(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
):
    async with sessionmaker() as session:
        scope_to_system(session)
        assert not await already_seeded(session)
        totals = await seed(session, DemoSettings(PASSWORD, TOTP_SECRET, specialists=2, clients=25))
        await session.commit()
        assert await already_seeded(session)
    assert totals["specialists"] == 2 and totals["clients"] >= 40 and totals["cases"] > 0

    assert (await login(client, specialist_email(1), PASSWORD)).status_code == 200
    index = (await client.post("/api/contacts/search", json={})).json()
    assert sum(index["counts"].values()) > 0 and index["counts"]["all"] >= 20
    own = {c["id"] for c in index["items"]}
    # Every card opens, and a vault password decrypts.
    contact = index["items"][0]["id"]
    assert (await client.get(f"/api/contacts/{contact}")).status_code == 200
    [mailbox, *_] = (await client.get("/api/vault/own")).json()
    shown = await client.post(f"/api/vault/entries/{mailbox['id']}/reveal")
    assert shown.status_code == 200 and len(shown.json()["password"]) >= 12
    assert (await client.get("/api/bookings/deadlines")).status_code == 200
    # Demo cases carry money: prices, payments and the counted balance and profit.
    work = (await client.post("/api/contacts/search", json={"tab": "work"})).json()["items"]
    priced = []
    for item in work[:10]:
        for case in (await client.get(f"/api/contacts/{item['id']}")).json()["cases"]:
            finance = (await client.get(f"/api/cases/{case['id']}/finance")).json()
            if finance["price"] is not None:
                priced.append(finance)
    assert priced and all(f["profit"] is not None for f in priced)
    assert any(f["payments"] for f in priced)
    # Settlements with registrars: talons charged, one advance runs low.
    ledgers = (await client.get("/api/registrars/ledgers")).json()
    assert len(ledgers) == 3 and sum(r["talons"] for r in ledgers) > 0
    assert any(r["low"] and not r["balance"].startswith("-") for r in ledgers)
    ledger = (await client.get(f"/api/registrars/{ledgers[0]['id']}/ledger")).json()
    assert ledger["lines"] and ledger["missing"] == len(ledger["missing_talons"])
    assert (await client.get("/api/queues/summary")).status_code == 200
    # Clients with a visa have trips for the 90/180 calculator.
    counted = []
    for item in index["items"]:
        stay = (await client.get(f"/api/contacts/{item['id']}/trips")).json()
        counted += [r for r in stay["stay"]["trips"] if r["counts"]]
    assert counted and all(r["days"] > 0 for r in counted)
    # A checklist for Poland: the shared items and the specialist's own.
    poland = next(
        c["id"] for c in (await client.get("/api/reference/countries")).json() if c["code"] == "PL"
    )
    checklist = (await client.get(f"/api/checklists/{poland}")).json()
    assert len(checklist["shared"]) == 8 and len(checklist["own"]) == 2
    # A few decided clients have a passport that goes in two days (backlog 2.8).
    purges = [
        r for r in (await client.get("/api/reminders")).json() if r["kind"] == "passport_purge"
    ]
    assert 0 < len(purges) <= PASSPORTS_TO_REMOVE
    assert all(r["days_left"] == 2 and r["passport_fields"] for r in purges)

    # The demo employee sees some of the clients: those of the cases given to them.
    client.cookies.clear()
    await login(client, "visa1.staff@demo.visacrm.test", PASSWORD)
    staff = (await client.post("/api/contacts/search", json={})).json()
    assert 0 < staff["counts"]["all"] < index["counts"]["all"]

    # The other demo specialist sees only their own clients.
    client.cookies.clear()
    await login(client, specialist_email(2), PASSWORD)
    other = {c["id"] for c in (await client.post("/api/contacts/search", json={})).json()["items"]}
    assert other and not other & own
    # Subscriptions: the first specialist is paid up, the second is overdue.
    assert (await client.get("/api/workspace")).json()["subscription"]["status"] == "overdue"

    client.cookies.clear()
    await login(client, specialist_email(1), PASSWORD)
    subscription = (await client.get("/api/subscription")).json()
    assert subscription["status"] == "paid" and len(subscription["payments"]) == 2

    client.cookies.clear()
    assert (await login(client, ADMIN_EMAIL, PASSWORD)).status_code == 200
    assert len((await client.get("/api/admin/specialists")).json()) == 2
    due = (await client.get("/api/admin/subscriptions/due")).json()
    assert [d["email"] for d in due] == [specialist_email(2)]

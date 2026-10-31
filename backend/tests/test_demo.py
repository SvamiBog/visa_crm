"""Demo data for the staging stand: it fills empty workspaces, works through the API like
real data and never runs in production."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import scope_to_system
from app.core.demo import (
    ADMIN_EMAIL,
    DemoRefused,
    DemoSettings,
    _user,
    already_seeded,
    check_settings,
    seed,
    specialist_email,
)
from app.models import UserRole
from tests.helpers import TOTP_SECRET, login

PASSWORD = "demo-password-42"


def test_demo_data_is_refused_outside_the_stand():
    with pytest.raises(DemoRefused):
        check_settings("production", PASSWORD, TOTP_SECRET)
    with pytest.raises(DemoRefused):
        check_settings("staging", "short", TOTP_SECRET)
    with pytest.raises(DemoRefused):
        check_settings("staging", PASSWORD, "not base32!")
    check_settings("staging", PASSWORD, TOTP_SECRET)
    check_settings("staging", PASSWORD, "")  # no secret: demo accounts without the code


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

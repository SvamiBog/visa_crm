"""The specialists' subscription to the CRM (roadmap 3.8, decisions.md → «Слоты и SaaS»):
tariffs, a workspace's subscription and the payments the admin marks."""

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.core.consultation_checks import add_months
from app.core.db import scope_to_system, scope_to_tenant
from app.models import SubscriptionPayment, Tariff
from tests.helpers import MakeUser, login, workspace_today


async def _pay(conn: AsyncConnection, tenant_id: uuid.UUID) -> None:
    await conn.execute(
        text(
            "INSERT INTO subscription_payments (id, tenant_id, paid_on, amount, currency) "
            "VALUES (gen_random_uuid(), :t, '2026-11-01', 50, 'EUR')"
        ),
        {"t": tenant_id},
    )


# --- Row-level security ------------------------------------------------------------------


async def test_payments_of_another_specialist_are_not_visible(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    boris = await make_user("boris@example.com")
    async with owner_engine.begin() as conn:
        await _pay(conn, anna)
        await _pay(conn, boris)
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert (await session.scalars(select(SubscriptionPayment.tenant_id))).all() == [anna]


async def test_a_specialist_cannot_write_its_payments_even_in_its_own_scope(
    owner_engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
):
    """Even a query the API never sends: only the admin (system scope) marks payments."""
    anna = await make_user("anna@example.com")
    async with owner_engine.begin() as conn:
        await _pay(conn, anna)
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert len((await session.scalars(select(SubscriptionPayment.id))).all()) == 1
        # Rows outside the write policy are skipped, not changed.
        updated = await session.execute(text("UPDATE subscription_payments SET amount = 1"))
        assert updated.rowcount == 0
        deleted = await session.execute(text("DELETE FROM subscription_payments"))
        assert deleted.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO subscription_payments (id, tenant_id, paid_on, amount, currency) "
                    "VALUES (gen_random_uuid(), :t, '2026-12-01', 50, 'EUR')"
                ),
                {"t": anna},
            )


async def test_tariffs_are_read_by_every_scope_and_written_only_by_the_system(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    async with sessionmaker() as session:
        scope_to_system(session)
        session.add(Tariff(name="Standard", price=50, currency="EUR"))
        await session.commit()
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert (await session.scalars(select(Tariff.name))).all() == ["Standard"]
        updated = await session.execute(text("UPDATE tariffs SET price = 1"))
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO tariffs (id, name, price, currency) "
                    "VALUES (gen_random_uuid(), 'Free', 1, 'EUR')"
                )
            )


# --- The admin's tariffs and subscriptions -----------------------------------------------


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    assert (await login(client, "root@example.com")).status_code == 200


async def switch_to(client: AsyncClient, email: str) -> None:
    client.cookies.clear()
    assert (await login(client, email)).status_code == 200


async def new_tariff(
    client: AsyncClient, name: str = "Standard", price: str = "50", currency: str = "EUR"
) -> dict:
    response = await client.post(
        "/api/admin/tariffs", json={"name": name, "price": price, "currency": currency}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def subscribe(
    client: AsyncClient,
    tenant_id: uuid.UUID | None,
    tariff: dict | None,
    starts_on: date | None = None,
    trial_until: date | None = None,
) -> Response:
    return await client.put(
        f"/api/admin/specialists/{tenant_id}/subscription",
        json={
            "tariff_id": tariff and tariff["id"],
            "starts_on": starts_on and starts_on.isoformat(),
            "trial_until": trial_until and trial_until.isoformat(),
        },
    )


async def pay(client: AsyncClient, tenant_id: uuid.UUID | None, **body) -> Response:
    body.setdefault("paid_on", workspace_today().isoformat())
    return await client.post(f"/api/admin/specialists/{tenant_id}/subscription/payments", json=body)


async def test_admin_keeps_tariffs_with_unique_names(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    tariff = await new_tariff(client)
    assert tariff | {"id": None} == {
        "id": None,
        "name": "Standard",
        "price": "50.00",
        "currency": "EUR",
        "is_active": True,
        "specialists": 0,
    }
    taken = await client.post(
        "/api/admin/tariffs", json={"name": "Standard", "price": "10", "currency": "BYN"}
    )
    assert taken.status_code == 409 and taken.json()["detail"] == "tariff_name_taken"
    for bad in ({"price": "0"}, {"currency": "GBP"}, {"name": " "}):
        body = {"name": "Pro", "price": "90", "currency": "EUR"} | bad
        assert (await client.post("/api/admin/tariffs", json=body)).status_code == 422

    changed = await client.patch(
        f"/api/admin/tariffs/{tariff['id']}", json={"price": "60", "is_active": False}
    )
    assert changed.status_code == 200
    assert (changed.json()["price"], changed.json()["is_active"]) == ("60.00", False)
    pro = await new_tariff(client, "Pro")
    # Names differ in more than the case.
    renamed = await client.patch(f"/api/admin/tariffs/{pro['id']}", json={"name": "standard"})
    assert renamed.status_code == 409 and renamed.json()["detail"] == "tariff_name_taken"
    # Active tariffs first.
    listed = [t["name"] for t in (await client.get("/api/admin/tariffs")).json()]
    assert listed == ["Pro", "Standard"]
    missing = await client.patch(f"/api/admin/tariffs/{uuid.uuid4()}", json={"price": "60"})
    assert missing.status_code == 404


async def test_admin_assigns_a_tariff_and_marks_payments(client: AsyncClient, make_user: MakeUser):
    anna = await make_user("anna@example.com")
    await as_admin(client, make_user)
    tariff = await new_tariff(client)
    starts_on = workspace_today() - timedelta(days=10)

    assigned = await subscribe(client, anna, tariff, starts_on)
    assert assigned.status_code == 200, assigned.text
    s = assigned.json()
    assert s["tariff"]["name"] == "Standard" and s["starts_on"] == starts_on.isoformat()
    assert (s["status"], s["next_due_on"], s["overdue_days"]) == (
        "overdue",
        starts_on.isoformat(),
        10,
    )

    # Two months by the tariff: the amount is counted when the admin leaves it out.
    first = await pay(client, anna, months=2)
    assert first.status_code == 201, first.text
    s = first.json()
    assert s["status"] == "paid"
    assert s["next_due_on"] == add_months(starts_on, 2).isoformat()
    assert [(p["months"], p["amount"], p["currency"]) for p in s["payments"]] == [
        (2, "100.00", "EUR")
    ]
    discounted = await pay(client, anna, amount="45", note="Скидка")
    assert discounted.status_code == 201
    payments = (await client.get(f"/api/admin/specialists/{anna}/subscription")).json()["payments"]
    assert [p["amount"] for p in payments] == ["45.00", "100.00"]  # the newest first
    assert payments[0]["note"] == "Скидка"

    removed = await client.delete(f"/api/admin/subscription-payments/{payments[0]['id']}")
    assert removed.status_code == 204
    s = (await client.get(f"/api/admin/specialists/{anna}/subscription")).json()
    assert s["next_due_on"] == add_months(starts_on, 2).isoformat()
    assert len(s["payments"]) == 1
    gone = await client.delete(f"/api/admin/subscription-payments/{payments[0]['id']}")
    assert gone.status_code == 404

    # Every change is filed under the specialist's workspace.
    log = await client.get("/api/admin/audit", params={"tenant_id": str(anna), "category": "admin"})
    actions = [e["action"] for e in log.json()["events"]]
    assert actions[:4] == [
        "admin.subscription_payment_deleted",
        "admin.subscription_payment_recorded",
        "admin.subscription_payment_recorded",
        "admin.subscription_updated",
    ]


async def test_a_subscription_needs_an_active_tariff_and_a_trial_after_the_start(
    client: AsyncClient, make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    await as_admin(client, make_user)
    today = workspace_today()
    old = await new_tariff(client, "Old")
    standard = await new_tariff(client)

    no_tariff = await pay(client, anna)
    assert no_tariff.status_code == 409 and no_tariff.json()["detail"] == "no_subscription"
    unknown = await subscribe(client, anna, {"id": str(uuid.uuid4())})
    assert unknown.status_code == 422 and unknown.json()["detail"] == "unknown_tariff"
    early = await subscribe(client, anna, standard, today, trial_until=today)
    assert early.status_code == 422 and early.json()["detail"] == "trial_before_start"
    nobody = await subscribe(client, uuid.uuid4(), standard)
    assert nobody.status_code == 404

    # Without a start date the subscription starts today; the trial puts off the payment.
    trial = await subscribe(client, anna, old, trial_until=today + timedelta(days=30))
    s = trial.json()
    assert (s["starts_on"], s["status"]) == (today.isoformat(), "trial")
    assert s["next_due_on"] == (today + timedelta(days=30)).isoformat()

    await client.patch(f"/api/admin/tariffs/{old['id']}", json={"is_active": False})
    kept = await subscribe(client, anna, old, today)  # already on it: kept
    assert kept.status_code == 200 and kept.json()["trial_until"] is None
    boris = await make_user("boris@example.com")
    inactive = await subscribe(client, boris, old)
    assert inactive.status_code == 422 and inactive.json()["detail"] == "tariff_inactive"

    ended = (await subscribe(client, anna, None)).json()
    assert (ended["tariff"], ended["starts_on"], ended["status"], ended["next_due_on"]) == (
        None,
        None,
        "none",
        None,
    )


async def test_a_new_price_applies_to_the_next_payment_not_to_past_ones(
    client: AsyncClient, make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    await as_admin(client, make_user)
    tariff = await new_tariff(client)
    await subscribe(client, anna, tariff)
    await pay(client, anna)
    await client.patch(f"/api/admin/tariffs/{tariff['id']}", json={"price": "70"})
    s = (await pay(client, anna)).json()
    assert s["tariff"]["price"] == "70.00"
    assert [p["amount"] for p in s["payments"]] == ["70.00", "50.00"]
    assert (await client.get("/api/admin/tariffs")).json()[0]["specialists"] == 1


async def test_the_due_list_has_overdue_and_upcoming_payments_of_active_specialists(
    client: AsyncClient, make_user: MakeUser
):
    overdue = await make_user("overdue@example.com")
    soon = await make_user("soon@example.com")
    paid = await make_user("paid@example.com")
    blocked = await make_user("blocked@example.com")
    await make_user("free@example.com")
    await as_admin(client, make_user)
    tariff = await new_tariff(client)
    today = workspace_today()
    await subscribe(client, overdue, tariff, today - timedelta(days=5))
    await subscribe(client, soon, tariff, today + timedelta(days=3))
    await subscribe(client, paid, tariff, today - timedelta(days=5))
    await pay(client, paid)
    await subscribe(client, blocked, tariff, today - timedelta(days=20))
    await client.post(f"/api/admin/specialists/{blocked}/block")

    due = (await client.get("/api/admin/subscriptions/due")).json()
    assert [(d["tenant_id"], d["status"]) for d in due] == [
        (str(overdue), "overdue"),
        (str(soon), "due_soon"),
    ]
    assert due[0]["overdue_days"] == 5 and due[0]["tariff"] == "Standard"

    every = (await client.get("/api/admin/subscriptions")).json()
    assert {s["email"]: s["status"] for s in every} == {
        "overdue@example.com": "overdue",
        "soon@example.com": "due_soon",
        "paid@example.com": "paid",
        "blocked@example.com": "overdue",
        "free@example.com": "none",
    }


# --- What the specialist sees ------------------------------------------------------------


async def test_the_specialist_sees_their_own_subscription_read_only(
    client: AsyncClient, make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    await make_user("boris@example.com")
    await as_admin(client, make_user)
    await subscribe(client, anna, await new_tariff(client))
    await pay(client, anna)

    await switch_to(client, "anna@example.com")
    own = await client.get("/api/subscription")
    assert own.status_code == 200
    assert own.json()["tariff"]["name"] == "Standard" and len(own.json()["payments"]) == 1
    assert (await client.get("/api/admin/tariffs")).status_code == 403
    assert (await pay(client, anna)).status_code == 403
    assert (await subscribe(client, anna, None)).status_code == 403

    await switch_to(client, "boris@example.com")
    other = (await client.get("/api/subscription")).json()
    assert (other["status"], other["payments"]) == ("none", [])


async def test_an_overdue_subscription_shows_to_the_specialist_but_not_to_employees(
    client: AsyncClient, make_user: MakeUser
):
    anna = await make_user("anna@example.com")
    await make_user("olga@example.com", role="employee", tenant_id=anna)
    await as_admin(client, make_user)
    today = workspace_today()
    await subscribe(client, anna, await new_tariff(client), today - timedelta(days=3))

    await switch_to(client, "anna@example.com")
    assert (await client.get("/api/workspace")).json()["subscription"] == {
        "status": "overdue",
        "next_due_on": (today - timedelta(days=3)).isoformat(),
        "overdue_days": 3,
    }

    await switch_to(client, "olga@example.com")
    assert (await client.get("/api/workspace")).json()["subscription"] is None
    assert (await client.get("/api/subscription")).status_code == 403

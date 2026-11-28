"""The specialist sets her base currency and tax rate herself (pilot T8, owner's answers of
2026-11-20): a new base currency counts again only what is kept in the base one, at the
site's latest National Bank rate (fake rates in conftest: BYN for one USD 3.1, EUR 3.4,
PLN 0.8); everything she entered keeps its amount and currency."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import import_jobs, rates
from tests.support.helpers import MakeUser, login
from tests.test_finances import countries, finance_url, open_case

MONEY = "/api/dictionaries/money"
CHANGED = "workspace.base_currency_changed"


async def specialist(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    email: str = "anna@example.com",
    base: str = "BYN",
) -> uuid.UUID:
    tenant_id = await make_user(email)
    assert tenant_id is not None
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET base_currency = :base, tax_rate = 10 WHERE id = :t"),
            {"base": base, "t": tenant_id},
        )
    client.cookies.clear()
    await login(client, email)
    return tenant_id


async def pay(client: AsyncClient, case: dict, amount: str, currency: str, **extra) -> dict:
    response = await client.post(
        f"/api/cases/{case['id']}/payments",
        json={"paid_on": "2026-05-15", "amount": amount, "currency": currency} | extra,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def changed_events(client: AsyncClient) -> list[dict]:
    events = (await client.get("/api/audit")).json()["events"]
    return [e for e in events if e["action"] == CHANGED]


def rows(items: list[dict]) -> list[tuple[str, str, str, str]]:
    return [(i["amount"], i["currency"], i["rate"], i["amount_base"]) for i in items]


async def test_a_new_base_counts_again_only_what_is_kept_in_the_base_one(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Another workspace in PLN, with a payment of its own.
    bella = await specialist(client, make_user, owner_engine, "bella@example.com", base="PLN")
    await pay(client, await open_case(client), "100", "USD")

    await specialist(client, make_user, owner_engine, base="PLN")
    first, second = await open_case(client), await open_case(client)
    saved = await client.patch(
        finance_url(first),
        json={
            "price": "1000",
            "registrar_fee_amount": "50",
            "registrar_fee_currency": "USD",
            "partner_terms": "fixed",
            "partner_fixed": "100",
        },
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["registrar_fee_rate"], saved.json()["registrar_fee_base"]) == (
        "3.875000",
        "193.75",
    )
    priced = await client.patch(finance_url(second), json={"currency": "USD", "price": "300"})
    assert priced.json()["rate"] == "3.875000", priced.text
    await pay(client, first, "100", "USD")
    await pay(client, first, "150", "BYN")
    await pay(client, first, "200", "PLN")
    # A rate she corrected by hand: to the old base.
    before = await pay(client, first, "50", "EUR", rate="4.5")
    assert [p["amount_base"] for p in before["payments"]] == [
        "387.50",
        "187.50",
        "200.00",
        "225.00",
    ]
    spent = await client.post(
        f"/api/cases/{first['id']}/expenses",
        json={"spent_on": "2026-05-16", "amount": "20", "currency": "EUR", "description": "x"},
    )
    assert spent.json()["expenses"][0]["amount_base"] == "85.00", spent.text
    poland = (await countries(client))["PL"]
    listed = await client.put(
        "/api/dictionaries/prepayments",
        json=[{"country_id": poland, "purpose_id": None, "amount": "120"}],
    )
    assert listed.status_code == 200, listed.text

    response = await client.patch(MONEY, json={"base_currency": "BYN"})
    assert response.status_code == 200, response.text
    # The old base stays accepted: her payments are in it.
    assert response.json()["base_currency"] == "BYN"
    assert response.json()["payment_currencies"] == ["BYN", "PLN", "USD", "EUR"]

    finance = (await client.get(finance_url(first))).json()
    assert finance["base_currency"] == "BYN"
    # The price keeps its currency; the case's rate is to the new base.
    assert (finance["currency"], finance["rate"], finance["price"]) == (
        "PLN",
        "0.800000",
        "1000.00",
    )
    # Each payment keeps its amount and currency; the bank's rate replaces the hand-made one.
    assert rows(finance["payments"]) == [
        ("100.00", "USD", "3.100000", "310.00"),
        ("150.00", "BYN", "1.000000", "150.00"),
        ("200.00", "PLN", "0.800000", "160.00"),
        ("50.00", "EUR", "3.400000", "170.00"),
    ]
    assert rows(finance["expenses"]) == [("20.00", "EUR", "3.400000", "68.00")]
    talon = [finance[f"registrar_fee_{k}"] for k in ("amount", "currency", "rate", "base")]
    assert talon == ["50.00", "USD", "3.100000", "155.00"]
    assert finance["partner_fixed"] == "80.00"  # 100 PLN
    # The balance is still in PLN: 1000 − (387.50 + 187.50 + 200 + 212.50).
    assert (finance["paid"], finance["balance"]) == ("987.50", "12.50")
    assert finance["price_base"] == "800.00"
    other = (await client.get(finance_url(second))).json()
    assert (other["currency"], other["rate"], other["price"]) == ("USD", "3.100000", "300.00")
    assert other["price_base"] == "930.00"
    # A prepayment of the list keeps the currency it was entered in (owner, 2026-11-20).
    prepayments = (await client.get("/api/dictionaries/prepayments")).json()
    assert [(p["amount"], p["currency"]) for p in prepayments] == [("120.00", "PLN")]

    # One record, codes and counts only.
    [event] = await changed_events(client)
    assert event["details"] == {
        "from": "PLN",
        "to": "BYN",
        "payments": 4,
        "expenses": 1,
        "talons": 1,
        "cases": 2,
        "partner_payouts": 1,
    }

    # The other workspace is untouched.
    async with owner_engine.connect() as conn:
        theirs = (
            await conn.execute(
                text(
                    "SELECT t.base_currency, p.rate, p.amount_base FROM case_payments p "
                    "JOIN tenants t ON t.id = p.tenant_id WHERE p.tenant_id = :t"
                ),
                {"t": bella},
            )
        ).one()
    assert tuple(map(str, theirs)) == ("PLN", "3.875000", "387.50")


async def test_a_missing_rate_changes_nothing(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await pay(client, case, "100", "EUR")
    monkeypatch.delitem(rates._latest, "EUR")  # the bank has not been reached for it

    response = await client.patch(MONEY, json={"base_currency": "PLN", "tax_rate": "5"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "base_rate_unavailable"

    money = (await client.get(MONEY)).json()
    assert (money["base_currency"], money["tax_rate"]) == ("BYN", "10.00")
    finance = (await client.get(finance_url(case))).json()
    assert rows(finance["payments"]) == [("100.00", "EUR", "3.400000", "340.00")]
    assert await changed_events(client) == []


async def test_a_workspace_without_amounts_needs_no_rate(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await specialist(client, make_user, owner_engine)
    monkeypatch.setattr(rates, "_latest", {})
    response = await client.patch(MONEY, json={"base_currency": "USD"})
    assert response.status_code == 200, response.text
    [event] = await changed_events(client)
    assert event["details"]["payments"] == 0


async def test_the_new_base_is_one_she_accepts(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await specialist(client, make_user, owner_engine)
    await client.patch(MONEY, json={"payment_currencies": ["USD"]})
    response = await client.patch(MONEY, json={"base_currency": "PLN"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "currency_not_accepted"
    assert (await client.get(MONEY)).json()["base_currency"] == "BYN"


async def test_not_while_an_import_writes(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = await specialist(client, make_user, owner_engine)
    await pay(client, await open_case(client), "100", "USD")
    job = import_jobs.ImportJob(
        id=uuid.uuid4(), tenant_id=tenant_id, kind="import", file_name="history.xlsx"
    )
    monkeypatch.setitem(import_jobs._jobs, job.id, job)

    response = await client.patch(MONEY, json={"base_currency": "PLN"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "import_running"
    assert (await client.get(MONEY)).json()["base_currency"] == "BYN"


async def test_an_employee_changes_neither_the_base_nor_the_tax(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await specialist(client, make_user, owner_engine)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    for body in ({"base_currency": "PLN"}, {"tax_rate": "5"}, {"tax_rate": None}):
        assert (await client.patch(MONEY, json=body)).status_code == 403
    assert (await client.get(MONEY)).json()["tax_rate"] == "10.00"


async def test_a_tax_rate_of_zero_is_no_tax_and_an_empty_one_is_not_set(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await client.patch(finance_url(case), json={"price": "300"})
    await pay(client, case, "300", "BYN", official=True)

    zero = await client.patch(MONEY, json={"tax_rate": "0"})
    assert (zero.status_code, zero.json()["tax_rate"]) == (200, "0.00")
    finance = (await client.get(finance_url(case))).json()
    assert (finance["tax_rate"], finance["tax"], finance["profit"]) == ("0.00", "0.00", "300.00")
    assert (await client.get("/api/workspace")).json()["tax_rate"] == "0.00"

    unset = await client.patch(MONEY, json={"tax_rate": None})
    assert (unset.status_code, unset.json()["tax_rate"]) == (200, None)
    finance = (await client.get(finance_url(case))).json()
    assert (finance["tax_rate"], finance["tax"], finance["profit"]) == (None, None, "300.00")

    ten = (await client.patch(MONEY, json={"tax_rate": "10"})).json()
    assert ten["tax_rate"] == "10.00"
    assert (await client.get(finance_url(case))).json()["tax"] == "30.00"
    assert (await client.patch(MONEY, json={"tax_rate": "-1"})).status_code == 422

    events = (await client.get("/api/audit")).json()["events"]
    assert [e["target_type"] for e in events if e["action"] == "reference.updated"] == [
        "tax_rate"
    ] * 3


async def test_the_admin_changing_the_base_counts_the_amounts_again_too(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await pay(client, case, "100", "USD")
    client.cookies.clear()
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")

    response = await client.patch(
        f"/api/admin/specialists/{tenant_id}", json={"base_currency": "PLN", "tax_rate": None}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["base_currency"], response.json()["tax_rate"]) == ("PLN", None)
    async with owner_engine.connect() as conn:
        payment = (
            await conn.execute(
                text("SELECT rate, amount_base FROM case_payments WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).one()
        logged = (
            (
                await conn.execute(
                    text("SELECT details FROM audit_events WHERE tenant_id = :t AND action = :a"),
                    {"t": tenant_id, "a": CHANGED},
                )
            )
            .scalars()
            .all()
        )
    assert tuple(map(str, payment)) == ("3.875000", "387.50")
    assert [d["to"] for d in logged] == ["PLN"]


async def test_a_new_workspace_has_no_tax_rate_until_one_is_set(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    created = await client.post(
        "/api/admin/specialists", json={"name": "Anna Visa", "email": "anna@example.com"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["specialist"]["tax_rate"] is None


async def test_a_tiny_partner_payout_stays_a_cent(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await client.patch(finance_url(case), json={"partner_terms": "fixed", "partner_fixed": "0.01"})
    # 0.01 BYN is 0.0032 USD: a payout of nothing would read as «not counted».
    assert (await client.patch(MONEY, json={"base_currency": "USD"})).status_code == 200
    assert (await client.get(finance_url(case))).json()["partner_fixed"] == "0.01"


async def test_a_payment_saved_during_a_change_waits_and_takes_the_new_base(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    # A change of the base currency holding the workspace's row, as PATCH /money does.
    async with owner_engine.connect() as conn:
        change = await conn.begin()
        await conn.execute(
            text("SELECT id FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id}
        )
        await conn.execute(
            text("UPDATE tenants SET base_currency = 'PLN' WHERE id = :t"), {"t": tenant_id}
        )
        saving = asyncio.create_task(pay(client, case, "100", "USD"))
        await asyncio.sleep(0.5)
        assert not saving.done(), "the payment waits for the change"
        await change.commit()
    finance = await saving
    assert rows(finance["payments"]) == [("100.00", "USD", "3.875000", "387.50")]


async def test_a_prepayment_of_the_list_is_converted_into_the_case_currency(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Another workspace's list does not reach this one.
    await specialist(client, make_user, owner_engine, "bella@example.com")
    poland = (await countries(client))["PL"]
    await client.put(
        "/api/dictionaries/prepayments",
        json=[{"country_id": poland, "purpose_id": None, "amount": "999", "currency": "EUR"}],
    )

    await specialist(client, make_user, owner_engine)  # base BYN
    listed = await client.put(
        "/api/dictionaries/prepayments",
        json=[{"country_id": poland, "purpose_id": None, "amount": "150"}],
    )
    assert [(p["amount"], p["currency"]) for p in listed.json()] == [("150.00", "BYN")]
    case = await open_case(client)
    hints = (await client.get(finance_url(case))).json()["suggestions"]
    assert hints["prepayment"] == "150.00"
    # 150 BYN in each currency the case may be priced in, rounded once.
    assert hints["prepayment_in"] == {
        "BYN": "150.00",
        "PLN": "187.50",
        "USD": "48.39",
        "EUR": "44.12",
    }
    priced = await client.patch(finance_url(case), json={"currency": "PLN"})
    assert priced.json()["suggestions"]["prepayment"] == "187.50"

    # A prepayment entered in USD stays in USD when the base currency changes.
    await client.put(
        "/api/dictionaries/prepayments",
        json=[{"country_id": poland, "purpose_id": None, "amount": "50", "currency": "USD"}],
    )
    assert (await client.patch(MONEY, json={"base_currency": "PLN"})).status_code == 200
    prepayments = (await client.get("/api/dictionaries/prepayments")).json()
    assert [(p["amount"], p["currency"]) for p in prepayments] == [("50.00", "USD")]
    hints = (await client.get(finance_url(case))).json()["suggestions"]
    assert (hints["prepayment"], hints["prepayment_in"]["USD"]) == ("193.75", "50.00")


async def test_the_admin_change_makes_the_new_base_accepted_and_logs_it_once(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await specialist(client, make_user, owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET payment_currencies = '{BYN,USD}' WHERE id = :t"),
            {"t": tenant_id},
        )
    client.cookies.clear()
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    url = f"/api/admin/specialists/{tenant_id}"
    assert (await client.patch(url, json={"base_currency": "PLN", "name": "Anna"})).is_success

    client.cookies.clear()
    await login(client, "anna@example.com")
    money = (await client.get(MONEY)).json()
    assert (money["base_currency"], money["payment_currencies"]) == ("PLN", ["PLN", "BYN", "USD"])
    async with owner_engine.connect() as conn:
        logged = (
            await conn.execute(
                text(
                    "SELECT action, details FROM audit_events WHERE tenant_id = :t "
                    "AND (action LIKE 'admin.%' OR action = :a) ORDER BY id"
                ),
                {"t": tenant_id, "a": CHANGED},
            )
        ).all()
    assert [(a, d.get("changed")) for a, d in logged] == [
        (CHANGED, None),
        ("admin.specialist_updated", ["name"]),
    ]


async def test_a_new_base_an_amount_would_not_fit_in_changes_nothing(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Audit H1: 300 000 000 000 EUR at a rate of 0.001 is 300 000 000 BYN, but
    # 1 275 000 000 000 PLN does not fit NUMERIC(14, 2).
    await specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await pay(client, case, "300000000000", "EUR", rate="0.001")
    response = await client.patch(MONEY, json={"base_currency": "PLN"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "base_amount_too_large"
    assert (await client.get(MONEY)).json()["base_currency"] == "BYN"
    finance = (await client.get(finance_url(case))).json()
    assert rows(finance["payments"]) == [("300000000000.00", "EUR", "0.001000", "300000000.00")]
    assert await changed_events(client) == []

    # A partner's fixed payout, kept in the base currency: 999 999 999 999 BYN is
    # 1 249 999 999 998.75 PLN.
    other = await open_case(client)
    await client.delete(f"/api/payments/{finance['payments'][0]['id']}")
    await client.patch(
        finance_url(other), json={"partner_terms": "fixed", "partner_fixed": "999999999999"}
    )
    response = await client.patch(MONEY, json={"base_currency": "PLN"})
    assert response.json()["detail"] == "base_amount_too_large"
    assert (await client.get(finance_url(other))).json()["partner_fixed"] == "999999999999.00"

    # The price of a case, in its currency, counted at the case's rate to the new base:
    # 300 000 000 000 EUR at 0.001 BYN, at 4.25 PLN would not fit.
    await client.patch(finance_url(other), json={"partner_terms": None})
    await client.patch(MONEY, json={"payment_currencies": ["EUR", "PLN"]})
    priced = await client.patch(
        finance_url(other), json={"currency": "EUR", "rate": "0.001", "price": "300000000000"}
    )
    assert priced.status_code == 200, priced.text
    response = await client.patch(MONEY, json={"base_currency": "PLN"})
    assert response.json()["detail"] == "base_amount_too_large"
    kept = (await client.get(finance_url(other))).json()
    assert (kept["currency"], kept["rate"], kept["price"]) == ("EUR", "0.001000", "300000000000.00")
    assert (await client.get(MONEY)).json()["base_currency"] == "BYN"

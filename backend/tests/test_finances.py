"""Case finances (backlog 1.9): price, payments in currencies with the National Bank's
rate, balance, talon, partner payout, tax and profit — in the base currency (BYN)."""

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import rates
from tests.conftest import FAKE_RATES_ON
from tests.support.helpers import MakeUser, login

DICT = "/api/dictionaries"


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, email="anna@example.com"
):
    tenant_id = await make_user(email)
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE tenants SET tax_rate = 10 WHERE id = :t"), {"t": tenant_id})
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def open_case(client: AsyncClient, contact: dict | None = None, **case) -> dict:
    contact = contact or (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    codes = await countries(client)
    response = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": contact["id"]}],
        }
        | case,
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


def finance_url(case: dict) -> str:
    return f"/api/cases/{case['id']}/finance"


async def test_the_prototype_case_adds_up(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    empty = (await client.get(finance_url(case))).json()
    assert empty["base_currency"] == "BYN" and empty["price"] is None
    assert empty["balance"] is None and empty["profit"] is None

    saved = await client.patch(
        finance_url(case),
        json={
            "price": "1350",
            "consultation_credit": "90",
            "registrar_fee_amount": "60",
            "registrar_fee_currency": "EUR",  # today's rate of the bank: 3.40
            "partner_terms": "percent",
            "partner_percent": "10",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["registrar_fee_base"] == "204.00"

    paid = await client.post(
        f"/api/cases/{case['id']}/payments",
        json={"paid_on": "2026-05-15", "amount": "150", "currency": "EUR", "official": True},
    )
    assert paid.status_code == 201, paid.text
    [payment] = paid.json()["payments"]
    assert (payment["rate"], payment["amount_base"]) == ("3.400000", "510.00")
    finance = (
        await client.post(
            f"/api/cases/{case['id']}/payments",
            json={"paid_on": "2026-06-01", "amount": "90", "currency": "BYN", "official": True},
        )
    ).json()
    assert finance["paid"] == "600.00"
    assert finance["balance"] == "660.00"  # 1350 − 90 − 600
    assert finance["partner_payout"] == "135.00"
    assert finance["tax"] == "60.00"
    assert finance["profit"] == "951.00"  # 1350 − 204 − 135 − 60

    spent = await client.post(
        f"/api/cases/{case['id']}/expenses",
        json={
            "spent_on": "2026-06-02",
            "amount": "10",
            "currency": "USD",
            "description": "Перевод справки",
        },
    )
    assert spent.json()["expenses_total"] == "31.00" and spent.json()["profit"] == "920.00"

    # Events say what changed, never how much.
    events = (await client.get("/api/audit")).json()["events"]
    finance_events = [e for e in events if e["action"].startswith("finances.")]
    assert {e["action"] for e in finance_events} == {
        "finances.updated",
        "finances.payment_added",
        "finances.expense_added",
    }
    for event in finance_events:
        assert set(event["details"]) <= {"case", "contact", "fields"}


async def test_a_rate_can_be_corrected_and_a_missing_one_entered(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    url = f"/api/cases/{case['id']}/payments"
    finance = (
        await client.post(url, json={"paid_on": "2026-05-15", "amount": "100", "currency": "PLN"})
    ).json()
    [payment] = finance["payments"]
    assert payment["amount_base"] == "80.00" and not payment["official"]

    corrected = await client.patch(f"/api/payments/{payment['id']}", json={"rate": "0.81"})
    assert corrected.json()["payments"][0]["amount_base"] == "81.00"
    # Another currency: the bank's rate for it again.
    switched = await client.patch(f"/api/payments/{payment['id']}", json={"currency": "USD"})
    assert switched.json()["payments"][0]["amount_base"] == "310.00"
    # A null for a field a payment always has leaves it as it is.
    kept = await client.patch(
        f"/api/payments/{payment['id']}", json={"official": None, "amount": None, "rate": None}
    )
    assert kept.status_code == 200, kept.text
    [same] = kept.json()["payments"]
    assert (same["official"], same["amount"], same["amount_base"]) == (False, "100.00", "310.00")

    # The site has no rate of the currency yet.
    monkeypatch.delitem(rates._latest, "EUR")
    missing = await client.post(
        url, json={"paid_on": "2030-01-01", "amount": "10", "currency": "EUR"}
    )
    assert missing.status_code == 422 and missing.json()["detail"] == "rate_unavailable"
    by_hand = await client.post(
        url, json={"paid_on": "2030-01-01", "amount": "10", "currency": "EUR", "rate": "3.5"}
    )
    assert by_hand.status_code == 201
    # Payments in the base currency need no rate.
    byn = await client.post(url, json={"paid_on": "2030-01-01", "amount": "5", "currency": "BYN"})
    assert byn.json()["paid"] == "350.00"  # 310 + 35 + 5

    deleted = await client.delete(f"/api/payments/{payment['id']}")
    assert deleted.json()["paid"] == "40.00"


async def test_a_request_takes_the_sites_latest_rate_and_never_asks_the_bank(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await as_specialist(client, make_user, owner_engine)

    def bank(currency, day):
        raise AssertionError("a request asked the National Bank")

    monkeypatch.setattr(rates, "_fetch", bank)
    # Any day: the site's latest rate (the owner's decision of 2026-10-28).
    for day in ("2024-03-01", "2026-10-15"):
        response = await client.get(f"/api/rates?currency=EUR&on={day}")
        assert response.json() == {
            "currency": "EUR",
            "base_currency": "BYN",
            "on": FAKE_RATES_ON.isoformat(),
            "rate": "3.400000",
        }
    assert (await client.get("/api/rates?currency=BYN")).json()["rate"] == "1"


async def test_the_site_keeps_todays_rates_from_the_bank(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await as_specialist(client, make_user, owner_engine)
    monkeypatch.setattr(rates, "_latest", {})
    calls = []

    def bank(currency, day):
        calls.append((currency, day))
        if currency == "PLN":
            raise rates.RateUnavailable(currency)
        return Decimal("3.5")

    monkeypatch.setattr(rates, "_fetch", bank)
    none_yet = await client.get("/api/rates?currency=EUR")
    assert none_yet.status_code == 422 and none_yet.json()["detail"] == "rate_unavailable"

    assert await rates.refresh(date(2026, 10, 28)) == ["PLN"]
    assert sorted(calls) == [(c, date(2026, 10, 28)) for c in ("EUR", "PLN", "USD")]
    eur = (await client.get("/api/rates?currency=EUR")).json()
    assert (eur["rate"], eur["on"]) == ("3.500000", "2026-10-28")
    pln = await client.get("/api/rates?currency=PLN")
    assert pln.status_code == 422 and pln.json()["detail"] == "rate_unavailable"

    # The bank is down the next day: the site keeps the rate it has.
    def offline(currency, day):
        raise rates.RateUnavailable(currency)

    monkeypatch.setattr(rates, "_fetch", offline)
    await rates.refresh(date(2026, 10, 29))
    assert (await client.get("/api/rates?currency=EUR")).json()["on"] == "2026-10-28"
    # After a restart the rate comes from the database.
    monkeypatch.setattr(rates, "_latest", {})
    assert (await client.get("/api/rates?currency=USD")).json()["rate"] == "3.500000"


async def test_suggestions_from_the_lists_and_the_consultation(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    purpose = (await client.post(f"{DICT}/purposes", json={"name": "Туризм"})).json()["id"]
    await client.put(
        f"{DICT}/prepayments",
        json=[
            {"country_id": codes["PL"], "purpose_id": None, "amount": "100"},
            {"country_id": codes["PL"], "purpose_id": purpose, "amount": "150"},
        ],
    )
    registrar = (
        await client.post(f"{DICT}/registrars", json={"name": "Иван", "kind": "individual"})
    ).json()
    await client.put(
        f"{DICT}/registrars/{registrar['id']}/prices",
        json=[
            {
                "country_id": codes["PL"],
                "purpose_id": None,
                "hall": "standard",
                "amount": "60",
                "currency": "EUR",
            }
        ],
    )
    partner = (await client.post(f"{DICT}/partners", json={"name": "Пётр"})).json()
    await client.put(
        f"{DICT}/partners/{partner['id']}/terms",
        json=[
            {"country_id": None, "terms_type": "percent", "percent": "10"},
            {"country_id": codes["PL"], "terms_type": "fixed", "amount": "20", "currency": "EUR"},
        ],
    )
    anna = (
        await client.post("/api/contacts", json={"first_name": "Анна", "partner_id": partner["id"]})
    ).json()
    consultation = (
        await client.post(
            f"/api/contacts/{anna['id']}/consultations",
            json={
                "held_on": "2026-05-10",
                "fee_amount": "30",
                "fee_currency": "USD",
                "fee_paid": True,
                "fee_paid_on": "2026-05-10",
            },
        )
    ).json()
    case = await open_case(
        client,
        anna,
        purpose_id=purpose,
        registrar_id=registrar["id"],
        consultation_id=consultation["id"],
        applicants=[{"contact_id": anna["id"]}, {"first_name": "Мария", "label": "дочь"}],
    )
    hints = (await client.get(finance_url(case))).json()["suggestions"]
    assert hints["prepayment"] == "150.00"  # the purpose's own wins over «any»
    fee = hints["registrar_fee"]
    assert (fee["amount"], fee["currency"], fee["rate"]) == ("60.00", "EUR", "3.400000")
    assert fee["on"]  # not queued yet: today
    assert (hints["partner_terms"], hints["partner_fixed"]) == ("fixed", "68.00")  # Poland's
    assert hints["consultation_credit"] == "93.00"  # 30 USD × 3.10
    # The daughter's case does not count the same consultation again.
    [daughter] = [m for m in case["group"]]
    other = (await client.get(f"/api/cases/{daughter['case_id']}/finance")).json()
    assert other["suggestions"]["consultation_credit"] is None


async def test_a_suggestion_taken_off_is_not_filled_in_again(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Backlog 1.15.12, owner's answer 2026-11-24: the form fills empty fields from the
    lists and the consultation. A field the specialist emptied — the list's prepayment,
    the consultation credit, the partner's terms — stays empty on the next save, until she
    types a value; another case still gets them."""
    await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    await client.put(
        f"{DICT}/prepayments",
        json=[{"country_id": codes["PL"], "purpose_id": None, "amount": "150"}],
    )
    partner = (await client.post(f"{DICT}/partners", json={"name": "Пётр"})).json()
    await client.put(
        f"{DICT}/partners/{partner['id']}/terms",
        json=[{"country_id": None, "terms_type": "percent", "percent": "10"}],
    )
    anna = (
        await client.post("/api/contacts", json={"first_name": "Анна", "partner_id": partner["id"]})
    ).json()
    consultation = (
        await client.post(
            f"/api/contacts/{anna['id']}/consultations",
            json={
                "held_on": "2026-05-10",
                "fee_amount": "90",
                "fee_currency": "BYN",
                "fee_paid": True,
                "fee_paid_on": "2026-05-10",
            },
        )
    ).json()
    case = await open_case(client, anna, consultation_id=consultation["id"])
    url = finance_url(case)
    hints = (await client.get(url)).json()["suggestions"]
    assert (hints["prepayment"], hints["consultation_credit"]) == ("150.00", "90.00")
    assert (hints["partner_terms"], hints["partner_percent"]) == ("percent", "10.00")

    # What the form sends with the three fields emptied, the price typed.
    form = {
        "price": "500",
        "prepayment": None,
        "consultation_credit": None,
        "partner_terms": None,
        "partner_percent": None,
        "partner_fixed": None,
        "partner_paid_on": None,
        "registrar_fee_amount": None,
    }
    cleared = await client.patch(url, json=form)
    assert cleared.status_code == 200, cleared.text
    hints = cleared.json()["suggestions"]
    assert (hints["prepayment"], hints["prepayment_in"], hints["consultation_credit"]) == (
        None,
        {},
        None,
    )
    assert (hints["partner_terms"], hints["partner_percent"], hints["partner_fixed"]) == (
        None,
        None,
        None,
    )
    # Saved again: nothing came back, the balance is the whole price.
    again = (await client.patch(url, json=form | {"price": "600"})).json()
    assert (again["prepayment"], again["partner_terms"]) == (None, None)
    assert Decimal(again["consultation_credit"]) == 0
    assert (again["balance"], again["suggestions"]["prepayment"]) == ("600.00", None)

    # Typed, they are hers; emptied again, they stay empty.
    hers = {"prepayment": "120", "consultation_credit": "50", "partner_terms": "none"}
    assert (await client.patch(url, json=hers)).status_code == 200
    typed = (await client.get(url)).json()
    assert (typed["prepayment"], typed["consultation_credit"], typed["partner_terms"]) == (
        "120.00",
        "50.00",
        "none",
    )
    emptied = (await client.patch(url, json=form)).json()
    assert (emptied["prepayment"], emptied["partner_terms"]) == (None, None)
    assert emptied["suggestions"]["partner_terms"] is None

    # Another client of the partner still starts from the lists.
    bella = (
        await client.post(
            "/api/contacts", json={"first_name": "Белла", "partner_id": partner["id"]}
        )
    ).json()
    other = await open_case(client, bella)
    fresh = (await client.get(finance_url(other))).json()["suggestions"]
    assert (fresh["prepayment"], fresh["partner_terms"]) == ("150.00", "percent")


async def test_partner_terms_must_be_complete(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    url = finance_url(case)
    missing = await client.patch(url, json={"partner_terms": "percent"})
    assert missing.status_code == 422 and missing.json()["detail"] == "partner_terms_incomplete"
    fee = await client.patch(url, json={"registrar_fee_amount": "60"})
    assert fee.status_code == 422 and fee.json()["detail"] == "registrar_fee_incomplete"

    await client.patch(
        url,
        json={
            "price": "500",
            "partner_terms": "percent",
            "partner_percent": "10",
            "partner_paid_on": "2026-06-01",
        },
    )
    # «Не указывать»: no payout, and the profit is shown without it.
    unspecified = (await client.patch(url, json={"partner_terms": "unspecified"})).json()
    assert unspecified["partner_percent"] is None and unspecified["partner_paid_on"] is None
    assert unspecified["partner_payout"] is None and unspecified["profit"] == "500.00"
    none = (await client.patch(url, json={"partner_terms": "none"})).json()
    assert none["partner_payout"] == "0.00"
    cleared = (await client.patch(url, json={"registrar_fee_amount": None})).json()
    assert cleared["registrar_fee_currency"] is None


async def test_finances_need_their_module_and_stay_in_the_workspace(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    finance = (
        await client.post(
            f"/api/cases/{case['id']}/payments",
            json={"paid_on": "2026-05-15", "amount": "100", "currency": "BYN"},
        )
    ).json()
    payment_id = finance["payments"][0]["id"]

    client.cookies.clear()
    await as_specialist(client, make_user, owner_engine, "boris@example.com")
    assert (await client.get(finance_url(case))).status_code == 404
    assert (await client.delete(f"/api/payments/{payment_id}")).status_code == 404

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    denied = await client.get(finance_url(case))
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"


async def test_the_price_can_be_named_in_another_accepted_currency(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Pilot fix 4: base BYN, the price in PLN, payments in BYN, PLN and USD.
    tenant_id = await as_specialist(client, make_user, owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET payment_currencies = '{PLN,USD}' WHERE id = :t"),
            {"t": tenant_id},
        )
    case = await open_case(client)
    url = finance_url(case)
    empty = (await client.get(url)).json()
    assert (empty["currency"], empty["rate"]) == ("BYN", "1")

    saved = await client.patch(url, json={"currency": "PLN", "price": "800"})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["currency"], saved.json()["rate"]) == ("PLN", "0.800000")
    for amount, currency in (("150", "BYN"), ("100", "PLN"), ("20", "USD")):
        response = await client.post(
            f"/api/cases/{case['id']}/payments",
            json={
                "paid_on": "2025-04-07",
                "amount": amount,
                "currency": currency,
                "official": currency == "BYN",
            },
        )
        assert response.status_code == 201, response.text
    finance = response.json()
    # Each payment against the price, in PLN, as income: never with a minus.
    assert [p["amount_case"] for p in finance["payments"]] == ["187.50", "100.00", "77.50"]
    assert finance["paid"] == "365.00"  # 187.50 + 100 + 77.50 PLN
    assert finance["balance"] == "435.00"  # PLN
    assert finance["price_base"] == "640.00"
    assert finance["tax"] == "15.00"  # 10% of 150 BYN
    assert finance["profit"] == "625.00"  # BYN

    # A corrected rate is kept; the base currency is always at 1.
    corrected = (await client.patch(url, json={"rate": "0.81"})).json()
    assert Decimal(corrected["rate"]) == Decimal("0.81") and corrected["price_base"] == "648.00"
    kept = (await client.patch(url, json={"price": "900"})).json()
    assert Decimal(kept["rate"]) == Decimal("0.81")
    back = (await client.patch(url, json={"currency": "BYN", "price": "300"})).json()
    assert (back["currency"], Decimal(back["rate"])) == ("BYN", Decimal("1"))
    assert back["balance"] == "8.00"  # 300 − 150 − 80 − 62 BYN

    # Only a currency the specialist accepts.
    refused = await client.patch(url, json={"currency": "EUR"})
    assert refused.status_code == 422 and refused.json()["detail"] == "currency_not_accepted"
    assert (await client.patch(url, json={"currency": None})).json()["currency"] == "BYN"
    # A case keeps its currency when the specialist stops accepting it.
    await client.patch(url, json={"currency": "PLN"})
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET payment_currencies = '{USD}' WHERE id = :t"), {"t": tenant_id}
        )
    still = await client.patch(url, json={"currency": "PLN", "price": "700"})
    assert still.status_code == 200 and still.json()["currency"] == "PLN", still.text

    events = (await client.get("/api/audit")).json()["events"]
    fields = {e["details"].get("fields") for e in events if e["action"] == "finances.updated"}
    assert "currency,price,rate" in fields


async def test_without_a_tax_rate_the_case_says_so_instead_of_zero(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Pilot fix 5: a workspace whose tax rate is not set yet (NULL, pilot T8).
    tenant_id = await as_specialist(client, make_user, owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET tax_rate = NULL WHERE id = :t"), {"t": tenant_id}
        )
    case = await open_case(client)
    for day in ("2025-04-07", "2025-05-08"):
        finance = (
            await client.post(
                f"/api/cases/{case['id']}/payments",
                json={"paid_on": day, "amount": "150", "currency": "BYN", "official": True},
            )
        ).json()
    # No price yet: no balance or profit, never a loss.
    assert (finance["paid"], finance["balance"], finance["profit"]) == ("300.00", None, None)
    assert finance["tax"] is None and finance["official"] == "300.00"
    priced = (await client.patch(finance_url(case), json={"price": "300"})).json()
    assert (priced["balance"], priced["tax"], priced["profit"]) == ("0.00", None, "300.00")


async def test_an_amount_too_large_for_the_base_currency_is_refused_with_its_code(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Audit H1: each field fits on its own, amount × rate did not fit NUMERIC(14, 2) and the
    # site answered 500. A rate typed without its decimal point: 34000000 for 3.4.
    tenant_id = await as_specialist(client, make_user, owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET payment_currencies = '{EUR}' WHERE id = :t"), {"t": tenant_id}
        )
    case = await open_case(client)
    payments, expenses = f"/api/cases/{case['id']}/payments", f"/api/cases/{case['id']}/expenses"
    slip = {"amount": "150000", "currency": "EUR", "rate": "34000000"}

    def refused(response) -> bool:
        return response.status_code == 422 and response.json()["detail"] == "amount_too_large"

    assert refused(await client.post(payments, json={"paid_on": "2026-05-15"} | slip))
    assert refused(
        await client.post(expenses, json={"spent_on": "2026-05-15", "description": "Х"} | slip)
    )
    # Up to the column's limit an amount is taken.
    at_limit = await client.post(
        payments, json={"paid_on": "2026-05-15", "amount": "999999999999.99", "currency": "BYN"}
    )
    assert at_limit.status_code == 201, at_limit.text
    paid = await client.post(
        payments, json={"paid_on": "2026-05-15", "amount": "100", "currency": "EUR"}
    )
    payment = next(p for p in paid.json()["payments"] if p["currency"] == "EUR")
    spent = await client.post(
        expenses,
        json={"spent_on": "2026-05-15", "amount": "10", "currency": "EUR", "description": "Х"},
    )
    [expense] = spent.json()["expenses"]
    assert refused(await client.patch(f"/api/payments/{payment['id']}", json=slip))
    assert refused(await client.patch(f"/api/expenses/{expense['id']}", json=slip))
    # The talon and the price of the case.
    assert refused(
        await client.patch(
            finance_url(case),
            json={
                "registrar_fee_amount": "150000",
                "registrar_fee_currency": "EUR",
                "registrar_fee_rate": "34000000",
            },
        )
    )
    assert refused(await client.patch(finance_url(case), json={"price": "150000"} | slip))

    # Nothing of the refused writes was kept.
    finance = (await client.get(finance_url(case))).json()
    assert sorted(p["amount_base"] for p in finance["payments"]) == [
        "340.00",
        "999999999999.99",
    ]
    assert [(e["amount"], e["amount_base"]) for e in finance["expenses"]] == [("10.00", "34.00")]
    assert finance["registrar_fee_amount"] is None and finance["price"] is None
    assert (finance["currency"], finance["rate"]) == ("BYN", "1")


async def test_an_expense_counts_in_the_base_currency_and_its_changes_move_the_profit(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # Audit H5: an expense is in the profit of the case, so each change of it is a change
    # of money: in the base currency at the rate, written to the journal without amounts.
    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    await client.patch(finance_url(case), json={"price": "1000"})
    added = await client.post(
        f"/api/cases/{case['id']}/expenses",
        json={
            "spent_on": "2026-06-02",
            "amount": "10",
            "currency": "USD",
            "description": "Перевод",
        },
    )
    assert added.status_code == 201, added.text
    [expense] = added.json()["expenses"]
    assert (expense["rate"], expense["amount_base"]) == ("3.100000", "31.00")
    assert (added.json()["expenses_total"], added.json()["profit"]) == ("31.00", "969.00")
    url = f"/api/expenses/{expense['id']}"

    def first(response) -> tuple:
        [e] = response.json()["expenses"]
        # The rate as a number: right after a change the answer has it as it was sent.
        return (e["spent_on"], e["amount"], e["currency"], Decimal(e["rate"]), e["amount_base"])

    more = await client.patch(url, json={"amount": "20"})
    assert more.status_code == 200, more.text
    assert first(more) == ("2026-06-02", "20.00", "USD", Decimal("3.1"), "62.00")
    assert more.json()["profit"] == "938.00"
    corrected = await client.patch(url, json={"rate": "3.2", "spent_on": "2026-06-03"})
    assert first(corrected) == ("2026-06-03", "20.00", "USD", Decimal("3.2"), "64.00")
    assert corrected.json()["profit"] == "936.00"
    # Into the base currency: at 1. A null description (it is required) leaves it as is.
    in_byn = await client.patch(url, json={"currency": "BYN", "description": None})
    assert first(in_byn) == ("2026-06-03", "20.00", "BYN", Decimal(1), "20.00")
    assert in_byn.json()["expenses"][0]["description"] == "Перевод"
    assert in_byn.json()["profit"] == "980.00"
    # The same values again change nothing and write no event.
    assert (await client.patch(url, json={"amount": "20"})).status_code == 200

    # Another workspace finds neither the expense nor a way to change it.
    client.cookies.clear()
    await as_specialist(client, make_user, owner_engine, "boris@example.com")
    assert (await client.patch(url, json={"amount": "1"})).status_code == 404
    assert (await client.delete(url)).status_code == 404
    client.cookies.clear()
    await login(client, "anna@example.com")
    assert first(await client.get(finance_url(case))) == (
        "2026-06-03",
        "20.00",
        "BYN",
        Decimal(1),
        "20.00",
    )

    deleted = await client.delete(url)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["expenses"] == []
    assert (deleted.json()["expenses_total"], deleted.json()["profit"]) == ("0.00", "1000.00")
    assert (await client.delete(url)).status_code == 404

    events = (await client.get("/api/audit")).json()["events"]
    expense_events = [e for e in events if e["action"].startswith("finances.expense_")]
    assert sorted(e["action"] for e in expense_events) == [
        "finances.expense_added",
        "finances.expense_deleted",
        "finances.expense_updated",
        "finances.expense_updated",
        "finances.expense_updated",
    ]
    updated = [e for e in expense_events if e["action"] == "finances.expense_updated"]
    assert sorted(sorted(e["details"]["fields"].split(",")) for e in updated) == [
        ["amount"],
        ["currency"],
        ["rate", "spent_on"],
    ]
    for event in expense_events:
        assert set(event["details"]) <= {"case", "contact", "fields"}
        assert event["details"]["case"] == case["id"]

"""Case finances (backlog 1.9): price, payments in currencies with the National Bank's
rate, balance, talon, partner payout, tax and profit — in the base currency (BYN)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import rates
from tests.helpers import MakeUser, login

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
    contact = contact or (await client.post("/api/contacts", json={"full_name": "Анна"})).json()
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

    def offline(currency, day):
        raise rates.RateUnavailable(currency)

    monkeypatch.setattr(rates, "_fetch", offline)
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


async def test_rates_are_asked_once_per_day(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await as_specialist(client, make_user, owner_engine)
    calls = []
    real = rates._fetch

    def counted(currency, day):
        calls.append((currency, day))
        return real(currency, day)

    monkeypatch.setattr(rates, "_fetch", counted)
    for _ in range(3):
        response = await client.get("/api/rates?currency=EUR&on=2026-10-15")
        assert response.json() == {
            "currency": "EUR",
            "base_currency": "BYN",
            "on": "2026-10-15",
            "rate": "3.400000",
        }
    assert len(calls) == 1
    assert (await client.get("/api/rates?currency=BYN&on=2026-10-15")).json()["rate"] == "1"


async def test_a_missing_rate_is_not_asked_again_at_once(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    await as_specialist(client, make_user, owner_engine)
    calls = []

    def offline(currency, day):
        calls.append((currency, day))
        raise rates.RateUnavailable(currency)

    monkeypatch.setattr(rates, "_fetch", offline)
    for _ in range(3):
        response = await client.get("/api/rates?currency=EUR&on=2030-01-01")
        assert response.status_code == 422 and response.json()["detail"] == "rate_unavailable"
    assert len(calls) == 1
    # After the pause the bank is asked again.
    monkeypatch.setattr(rates, "RETRY_AFTER", 0)
    await client.get("/api/rates?currency=EUR&on=2030-01-01")
    assert len(calls) == 2


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
        await client.post("/api/contacts", json={"full_name": "Анна", "partner_id": partner["id"]})
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
        applicants=[{"contact_id": anna["id"]}, {"full_name": "Мария", "label": "дочь"}],
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

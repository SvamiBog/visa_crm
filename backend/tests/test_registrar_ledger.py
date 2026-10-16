"""Settlements with registrars (backlog 1.10): payments to a registrar and the talons of
the clients they queued, in the registrar's currency; the balance is an advance or a debt."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

DICT = "/api/dictionaries"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def registrar(
    client: AsyncClient, codes: dict, prices=(("PL", "60", "EUR"),), name="Иван", **extra
) -> dict:
    created = await client.post(
        f"{DICT}/registrars", json={"name": name, "kind": "individual", **extra}
    )
    assert created.status_code == 201, created.text
    reg = created.json()
    await client.put(
        f"{DICT}/registrars/{reg['id']}/prices",
        json=[
            {
                "country_id": codes[code],
                "purpose_id": None,
                "hall": "standard",
                "amount": amount,
                "currency": currency,
            }
            for code, amount, currency in prices
        ],
    )
    return reg


async def open_case(client: AsyncClient, codes: dict, name: str, reg: dict, country="PL") -> dict:
    contact = (await client.post("/api/contacts", json={"full_name": name})).json()
    response = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": codes[country],
            "submission_country_id": codes["BY"],
            "registrar_id": reg["id"],
            "applicants": [{"contact_id": contact["id"]}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


def ledger_url(reg: dict) -> str:
    return f"/api/registrars/{reg['id']}/ledger"


async def test_an_advance_is_spent_by_the_talons(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    reg = await registrar(client, codes, currency="EUR", low_balance="200")
    anna = await open_case(client, codes, "Анна", reg)
    queued = await client.post(f"/api/cases/{anna['id']}/queue", json={"queued_on": "2026-05-10"})
    assert queued.status_code == 201, queued.text
    boris = await open_case(client, codes, "Борис", reg)

    paid = await client.post(
        f"/api/registrars/{reg['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "300", "method": "transfer"},
    )
    assert paid.status_code == 201, paid.text
    ledger = paid.json()
    assert (ledger["currency"], ledger["balance"], ledger["low"]) == ("EUR", "300.00", False)
    # Both clients have no talon yet; the queued one comes first, with the price.
    assert [
        (m["contact_name"], m["queued_on"], m["price"], m["price_currency"])
        for m in ledger["missing_talons"]
    ] == [
        ("Анна", "2026-05-10", "60.00", "EUR"),
        ("Борис", None, "60.00", "EUR"),
    ]

    charged = await client.post(f"/api/registrars/{reg['id']}/talons", json={"case_id": anna["id"]})
    assert charged.status_code == 201, charged.text
    assert charged.json()["balance"] == "240.00"
    finance = (await client.get(f"/api/cases/{anna['id']}/finance")).json()
    assert finance["registrar_fee_on"] == "2026-05-10"  # the day the client was queued
    assert (finance["registrar_fee_rate"], finance["registrar_fee_base"]) == ("3.400000", "204.00")
    again = await client.post(f"/api/registrars/{reg['id']}/talons", json={"case_id": anna["id"]})
    assert again.status_code == 409 and again.json()["detail"] == "talon_exists"

    # A talon entered in the case finances counts the same.
    await client.patch(
        f"/api/cases/{boris['id']}/finance",
        json={"registrar_fee_amount": "60", "registrar_fee_currency": "EUR"},
    )
    ledger = (await client.get(ledger_url(reg))).json()
    assert (ledger["paid"], ledger["charged"], ledger["balance"]) == ("300.00", "120.00", "180.00")
    assert (ledger["talons"], ledger["missing"], ledger["low"]) == (2, 0, True)
    assert [(line["kind"], line["contact_name"], line["balance"]) for line in ledger["lines"]] == [
        ("talon", "Борис", "180.00"),
        ("talon", "Анна", "240.00"),
        ("payment", None, "300.00"),
    ]

    [summary] = (await client.get("/api/registrars/ledgers")).json()
    assert (summary["balance"], summary["low"], summary["last_payment_on"]) == (
        "180.00",
        True,
        "2026-05-01",
    )

    events = (await client.get("/api/audit")).json()["events"]
    ledger_events = [
        e
        for e in events
        if e["action"] in {"finances.registrar_payment_added", "finances.talon_charged"}
    ]
    assert len(ledger_events) == 2
    for event in ledger_events:
        assert set(event["details"]) <= {"case", "contact", "registrar", "fields"}


async def test_payments_are_corrected_and_removed(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    reg = await registrar(client, await countries(client), currency="EUR")
    url = f"/api/registrars/{reg['id']}/payments"
    assert (
        await client.post(url, json={"paid_on": "2026-05-01", "amount": "0", "method": "cash"})
    ).status_code == 422
    assert (
        await client.post(
            url, json={"paid_on": "2026-05-01", "amount": "10", "method": "cash", "talons": 0}
        )
    ).status_code == 422

    ledger = (
        await client.post(
            url,
            json={"paid_on": "2026-05-01", "amount": "100", "method": "cash", "note": "  "},
        )
    ).json()
    [line] = ledger["lines"]
    assert (line["method"], line["talons"], line["note"]) == ("cash", None, None)

    corrected = await client.patch(
        f"/api/registrar-payments/{line['payment_id']}",
        json={"amount": "120", "talons": 2, "note": "за Анну и Бориса"},
    )
    [line] = corrected.json()["lines"]
    assert (line["amount"], line["talons"], corrected.json()["balance"]) == ("120.00", 2, "120.00")
    as_advance = await client.patch(
        f"/api/registrar-payments/{line['payment_id']}", json={"talons": None}
    )
    assert as_advance.json()["lines"][0]["talons"] is None

    removed = await client.delete(f"/api/registrar-payments/{line['payment_id']}")
    assert (removed.json()["lines"], removed.json()["balance"]) == ([], "0.00")


async def test_other_currencies_and_missing_prices(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    reg = await registrar(client, codes, prices=(("PL", "50", "USD"),), currency="EUR")
    other = await registrar(client, codes, name="Мария", currency="EUR")
    poland = await open_case(client, codes, "Анна", reg)
    italy = await open_case(client, codes, "Борис", reg, country="IT")
    elsewhere = await open_case(client, codes, "Вера", other)

    # A talon in another currency than the registrar's is listed, not counted.
    ledger = (
        await client.post(f"/api/registrars/{reg['id']}/talons", json={"case_id": poland["id"]})
    ).json()
    assert (ledger["balance"], ledger["talons"]) == ("0.00", 0)
    [foreign] = ledger["other_currency"]
    assert (foreign["contact_name"], foreign["amount"], foreign["currency"]) == (
        "Анна",
        "50.00",
        "USD",
    )

    no_price = await client.post(
        f"/api/registrars/{reg['id']}/talons", json={"case_id": italy["id"]}
    )
    assert no_price.status_code == 422 and no_price.json()["detail"] == "no_registrar_price"
    [missing] = (await client.get(ledger_url(reg))).json()["missing_talons"]
    assert (missing["contact_name"], missing["price"]) == ("Борис", None)

    mismatch = await client.post(
        f"/api/registrars/{reg['id']}/talons", json={"case_id": elsewhere["id"]}
    )
    assert mismatch.status_code == 422 and mismatch.json()["detail"] == "registrar_mismatch"


async def test_the_talon_day_follows_the_queue(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    reg = await registrar(client, codes, currency="EUR")
    case = await open_case(client, codes, "Анна", reg)
    await client.post(f"/api/cases/{case['id']}/queue", json={"queued_on": "2026-05-10"})
    url = f"/api/cases/{case['id']}/finance"

    talon = {"registrar_fee_amount": "60", "registrar_fee_currency": "EUR"}
    assert (await client.patch(url, json=talon)).json()["registrar_fee_on"] == "2026-05-10"
    moved = (await client.patch(url, json={"registrar_fee_on": "2026-05-12"})).json()
    assert moved["registrar_fee_on"] == "2026-05-12"
    missing_day = await client.patch(url, json={"registrar_fee_on": None})
    assert missing_day.status_code == 422
    assert missing_day.json()["detail"] == "registrar_fee_incomplete"
    cleared = (await client.patch(url, json={"registrar_fee_amount": None})).json()
    assert cleared["registrar_fee_on"] is None and cleared["registrar_fee_currency"] is None


async def test_registrar_currency_and_threshold(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    created = (
        await client.post(f"{DICT}/registrars", json={"name": "Иван", "kind": "company"})
    ).json()
    assert (created["currency"], created["low_balance"]) == ("BYN", None)  # the base currency
    url = f"{DICT}/registrars/{created['id']}"
    updated = (await client.patch(url, json={"currency": "EUR", "low_balance": "50"})).json()
    assert (updated["currency"], updated["low_balance"]) == ("EUR", "50.00")
    assert (await client.patch(url, json={"low_balance": None})).json()["low_balance"] is None
    assert (await client.patch(url, json={"low_balance": "-1"})).status_code == 422
    assert (await client.patch(url, json={"currency": "RUB"})).status_code == 422


async def test_ledgers_need_finances_and_stay_in_the_workspace(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    reg = await registrar(client, await countries(client), currency="EUR")
    paid = await client.post(
        f"/api/registrars/{reg['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "100", "method": "cash"},
    )
    payment_id = paid.json()["lines"][0]["payment_id"]

    client.cookies.clear()
    await as_specialist(client, make_user, "boris@example.com")
    assert (await client.get("/api/registrars/ledgers")).json() == []
    assert (await client.get(ledger_url(reg))).status_code == 404
    assert (await client.delete(f"/api/registrar-payments/{payment_id}")).status_code == 404
    stray = await client.post(
        f"/api/registrars/{reg['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "1", "method": "cash"},
    )
    assert stray.status_code == 404

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    denied = await client.get("/api/registrars/ledgers")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

"""Deleting a case (pilot fix 1, docs/decisions.md → «Пилот: правки визовика (2026-11-19)»,
T1; owner's decision 2026-11-24 → «Удаление кейса и карточки со всем содержимым»): the
specialist only; the case goes at once with everything it holds, its money too, after a
question that names it all; vault accounts and templates stay with the client; its number
is never given again; a family group is rebuilt without it."""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support.helpers import CLOCK, PASSWORD, MakeUser, give_cases, login, workspace_today
from tests.test_cases import CONTACTS, as_specialist, countries, new_contact, open_case
from tests.test_data_import import CASES as IMPORTED_CASES
from tests.test_data_import import CLIENTS as IMPORTED_CLIENTS
from tests.test_data_import import run
from tests.test_employee_cases import EMPLOYEE, switch_to, with_employee
from tests.test_registrar_ledger import ledger_url, registrar


async def one_case(client: AsyncClient, contact_id: str, destination="PL", **fields) -> dict:
    response = await open_case(client, contact_id, destination, **fields)
    assert response.status_code == 201, response.text
    return response.json()[0]


async def rows_of(owner_engine: AsyncEngine, case_id: str) -> dict[str, int]:
    tables = (
        "application_attempts",
        "waiting_list_entries",
        "package_items",
        "bookings",
        "case_finances",
        "case_payments",
        "case_expenses",
        "employee_cases",
    )
    async with owner_engine.connect() as conn:
        return {
            table: (
                await conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE case_id = :c"), {"c": case_id}
                )
            ).scalar_one()
            for table in tables
        }


async def test_a_duplicate_case_is_deleted(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    naumik = await new_contact(client, last_name="Naumik", first_name="Mikalai")
    kept = await one_case(client, naumik["id"])
    duplicate = await one_case(client, naumik["id"])
    url = f"/api/cases/{duplicate['id']}"

    summary = (await client.get(f"{url}/deletion")).json()
    assert (summary["payments"], summary["payments_total"]) == (0, "0.00")

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    cases = (await client.get(f"{CONTACTS}/{naumik['id']}")).json()["cases"]
    assert [c["id"] for c in cases] == [kept["id"]]
    board = (await client.get("/api/cases/board")).json()["cases"]
    assert duplicate["id"] not in {c["id"] for c in board}

    events = (await client.get("/api/audit")).json()["events"]
    deleted = [e for e in events if e["action"] == "cases.deleted"]
    assert [e["target_id"] for e in deleted] == [duplicate["id"]]
    details = deleted[0]["details"]
    assert (details["contact"], details["number"]) == (naumik["id"], duplicate["number"])
    assert "Naumik" not in str(details)


async def test_what_goes_with_the_case(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Attempts, the waiting list, the package marks, bookings, the result, the
    employee's grant and a finance row without money go with the case."""
    tenant_id = await with_employee(client, make_user, owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET modules = modules || '{package_check}' WHERE id = :t"),
            {"t": tenant_id},
        )
    anna = await new_contact(client)
    france = await one_case(client, anna["id"], "FR")
    poland = await one_case(client, anna["id"])
    today = workspace_today().isoformat()
    sent = await client.post(f"/api/cases/{france['id']}/attempts", json={"sent_on": today})
    assert sent.status_code == 201, sent.text
    queued = await client.post(f"/api/cases/{poland['id']}/queue", json={"queued_on": today})
    assert queued.status_code == 201, queued.text
    codes = await countries(client)
    item = await client.post(f"/api/checklists/{codes['PL']}/items", json={"name": "Справка"})
    assert item.status_code == 201, item.text
    marked = await client.put(
        f"/api/cases/{poland['id']}/package/{item.json()['id']}", json={"state": "present"}
    )
    assert marked.status_code == 200, marked.text
    booked = await client.post(
        f"/api/cases/{poland['id']}/bookings",
        json={"kind": "hotel", "place": "Отель", "starts_on": today, "ends_on": today},
    )
    assert booked.status_code == 201, booked.text
    result = await client.patch(
        f"/api/cases/{poland['id']}", json={"outcome": "refusal", "decided_on": today}
    )
    assert result.status_code == 200, result.text
    price = await client.patch(f"/api/cases/{poland['id']}/finance", json={"price": "300"})
    assert price.status_code == 200, price.text
    await give_cases(owner_engine, EMPLOYEE, poland["id"])

    summary = (await client.get(f"/api/cases/{poland['id']}/deletion")).json()
    assert summary == summary | {
        "payments": 0,
        "queue_entries": 1,
        "package_items": 1,
        "bookings": 1,
        "outcomes": 1,
        "employees": 1,
        # The price goes too, and the client's balance with it (backlog 1.15.12).
        "terms": 1,
    }
    france_goes = (await client.get(f"/api/cases/{france['id']}/deletion")).json()
    assert (france_goes["attempts"], france_goes["terms"]) == (1, 0)

    for case in (france, poland):
        assert (await client.delete(f"/api/cases/{case['id']}")).status_code == 204
        assert set((await rows_of(owner_engine, case["id"])).values()) == {0}


async def test_only_the_specialist_deletes_a_case(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna = await new_contact(client)
    case = await one_case(client, anna["id"])
    await give_cases(owner_engine, EMPLOYEE, case["id"])
    await switch_to(client, EMPLOYEE)
    assert (await client.get(f"/api/cases/{case['id']}/deletion")).status_code == 403
    assert (await client.delete(f"/api/cases/{case['id']}")).status_code == 403
    assert (await client.get(f"/api/cases/{case['id']}")).status_code == 200


async def test_a_case_is_deleted_with_its_money(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Owner's decision 2026-11-24: money no longer holds a case. The question names the
    payments with their sum in the base currency, the expenses, the talon (the registrar's
    balance changes) and the partner payout marked as paid; the case goes with all of them,
    while the vault account given for it stays with the client."""
    await as_specialist(client, make_user)
    codes = await countries(client)
    ivan = await registrar(client, codes, currency="EUR")  # Poland: 60 EUR
    anna = await new_contact(client)
    case = await one_case(client, anna["id"], registrar_id=ivan["id"])
    url = f"/api/cases/{case['id']}"
    for amount, currency in (("150", "BYN"), ("100", "EUR")):
        payment = await client.post(
            f"{url}/payments",
            json={"paid_on": "2026-05-15", "amount": amount, "currency": currency},
        )
        assert payment.status_code == 201, payment.text
    expense = await client.post(
        f"{url}/expenses",
        json={"spent_on": "2026-05-15", "amount": "10", "currency": "BYN", "description": "Фото"},
    )
    assert expense.status_code == 201, expense.text
    paid = await client.post(
        f"/api/registrars/{ivan['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "60", "method": "cash", "talons": 1},
    )
    assert paid.status_code == 201, paid.text
    charged = await client.post(
        f"/api/registrars/{ivan['id']}/talons", json={"case_id": case["id"]}
    )
    assert charged.status_code == 201 and charged.json()["balance"] == "0.00"
    terms = {"price": "500", "partner_terms": "fixed", "partner_fixed": "50"}
    partner = await client.patch(f"{url}/finance", json=terms | {"partner_paid_on": "2026-06-01"})
    assert partner.status_code == 200, partner.text
    mail = await client.post(
        "/api/dictionaries/vault-systems", json={"name": "Почта", "kind": "email"}
    )
    assert mail.status_code == 201, mail.text
    account = await client.post(
        "/api/vault/entries",
        json={
            "system_id": mail.json()["id"],
            "contact_id": anna["id"],
            "case_id": case["id"],
            "login": "anna",
        },
    )
    assert account.status_code == 201, account.text
    payments_base = sum(
        Decimal(p["amount_base"]) for p in (await client.get(f"{url}/finance")).json()["payments"]
    )

    summary = (await client.get(f"{url}/deletion")).json()
    assert summary == summary | {
        "payments": 2,
        "payments_total": f"{payments_base:.2f}",
        "expenses": 1,
        "talons": 1,
        "partner_payouts": 1,
        "terms": 1,
        "vault_entries": 1,
        "templates": 0,
    }

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert set((await rows_of(owner_engine, case["id"])).values()) == {0}
    # The talon left the registrar's balance; the payment for it is an advance now.
    ledger = (await client.get(ledger_url(ivan))).json()
    assert (ledger["paid"], ledger["charged"], ledger["balance"]) == ("60.00", "0.00", "60.00")
    # The account stays with the client, no longer tied to the case.
    async with owner_engine.connect() as conn:
        kept = (
            await conn.execute(
                text("SELECT contact_id, case_id FROM vault_entries WHERE id = :e"),
                {"e": account.json()["id"]},
            )
        ).one()
    assert (str(kept.contact_id), kept.case_id) == (anna["id"], None)


def the_form(finance: dict, **changes) -> dict:
    """What «Стоимость и условия» sends on «Сохранить»: every field of the form, the
    talon's amount as the field shows it — saved, else the registrar's price filled in."""
    fee = finance["suggestions"]["registrar_fee"]
    amount = finance["registrar_fee_amount"] or (fee and fee["amount"])
    sent = {
        "currency": finance["currency"],
        "price": finance["price"],
        "prepayment": finance["prepayment"],
        "partner_terms": finance["partner_terms"],
        "partner_fixed": finance["partner_fixed"],
        "partner_paid_on": finance["partner_paid_on"],
        "registrar_fee_amount": amount,
    }
    if amount is not None:
        sent |= {
            "registrar_fee_currency": finance["registrar_fee_currency"] or fee["currency"],
            "registrar_fee_on": finance["registrar_fee_on"] or fee["on"],
        }
    return sent | changes


async def test_a_talon_taken_off_stays_off_and_the_case_goes(
    client: AsyncClient, make_user: MakeUser
):
    """Backlog 1.15.12: the refusal says to take the talon off in the case finances. Taken
    off, it is not filled in again from the registrar's price list, so the next save of
    the finances — the partner's date the same refusal names — does not charge it again,
    and the case goes. The registrar was paid for that talon: the payment stays in the
    settlements, as an advance."""
    await as_specialist(client, make_user)
    codes = await countries(client)
    ivan = await registrar(client, codes, currency="EUR")  # Poland: 60 EUR
    anna = await new_contact(client)
    case = await one_case(client, anna["id"], registrar_id=ivan["id"])
    url = f"/api/cases/{case['id']}"
    fresh = (await client.get(f"{url}/finance")).json()
    # A case of the registrar starts with the talon of the price list in the form.
    assert fresh["suggestions"]["registrar_fee"]["amount"] == "60.00"

    paid = await client.post(
        f"/api/registrars/{ivan['id']}/payments",
        json={"paid_on": "2026-05-01", "amount": "60", "method": "cash", "talons": 1},
    )
    assert paid.status_code == 201, paid.text
    charged = await client.post(
        f"/api/registrars/{ivan['id']}/talons", json={"case_id": case["id"]}
    )
    assert charged.status_code == 201 and charged.json()["balance"] == "0.00"
    terms = {"price": "500", "partner_terms": "fixed", "partner_fixed": "50"}
    partner = await client.patch(f"{url}/finance", json=terms | {"partner_paid_on": "2026-06-01"})
    assert partner.status_code == 200, partner.text
    summary = (await client.get(f"{url}/deletion")).json()
    assert (summary["talons"], summary["partner_payouts"]) == (1, 1)

    # The talon's amount emptied in the form.
    off = await client.patch(
        f"{url}/finance", json=the_form(partner.json(), registrar_fee_amount=None)
    )
    assert off.status_code == 200, off.text
    finance = off.json()
    assert finance["registrar_fee_amount"] is None
    assert finance["suggestions"]["registrar_fee"] is None
    # It left the registrar's balance; the payment for it is an advance now.
    ledger = (await client.get(ledger_url(ivan))).json()
    assert (ledger["paid"], ledger["charged"], ledger["balance"]) == ("60.00", "0.00", "60.00")
    assert [line["kind"] for line in ledger["lines"]] == ["payment"]

    # The form opened again: the talon stays off when the partner's date is cleared.
    finance = (await client.get(f"{url}/finance")).json()
    again = await client.patch(f"{url}/finance", json=the_form(finance, partner_paid_on=None))
    assert again.status_code == 200, again.text
    assert again.json()["registrar_fee_amount"] is None
    assert (await client.get(f"{url}/deletion")).json()["talons"] == 0
    assert (await client.delete(url)).status_code == 204
    ledger = (await client.get(ledger_url(ivan))).json()
    assert (ledger["balance"], ledger["talons"], ledger["missing"]) == ("60.00", 0, 0)


async def test_a_talon_paid_before_the_crm_is_taken_off_too(
    client: AsyncClient, make_user: MakeUser
):
    """A talon from the tables, paid to the registrar before the CRM, is not in his
    balance: taking it off changes the case's profit only."""
    await as_specialist(client, make_user)
    codes = await countries(client)
    ivan = await registrar(client, codes, currency="EUR")
    anna = await new_contact(client)
    case = await one_case(client, anna["id"], registrar_id=ivan["id"])
    url = f"/api/cases/{case['id']}"
    settled = await client.patch(
        f"{url}/finance",
        json={
            "price": "500",
            "registrar_fee_amount": "60",
            "registrar_fee_currency": "EUR",
            "registrar_fee_settled": True,
        },
    )
    assert settled.status_code == 200, settled.text
    assert (await client.get(ledger_url(ivan))).json()["charged"] == "0.00"

    off = await client.patch(
        f"{url}/finance", json=the_form(settled.json(), registrar_fee_amount=None)
    )
    assert off.status_code == 200, off.text
    assert (off.json()["registrar_fee_settled"], off.json()["profit"]) == (False, "500.00")
    finance = (await client.get(f"{url}/finance")).json()
    assert finance["suggestions"]["registrar_fee"] is None
    assert (await client.patch(f"{url}/finance", json=the_form(finance))).status_code == 200
    assert (await client.delete(url)).status_code == 204
    assert (await client.get(ledger_url(ivan))).json()["balance"] == "0.00"


async def test_a_registrar_without_a_talon_is_not_named_as_a_talon(
    client: AsyncClient, make_user: MakeUser
):
    """The registrar chosen in the case's form is not money: only a talon is."""
    await as_specialist(client, make_user)
    codes = await countries(client)
    ivan = await registrar(client, codes, currency="EUR")
    anna = await new_contact(client)
    case = await one_case(client, anna["id"], registrar_id=ivan["id"])
    summary = (await client.get(f"/api/cases/{case['id']}/deletion")).json()
    assert summary["talons"] == 0
    assert (await client.delete(f"/api/cases/{case['id']}")).status_code == 204
    assert (await client.get(ledger_url(ivan))).json()["missing"] == 0


async def test_another_specialist_cannot_delete_a_case(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    case = await one_case(client, anna["id"])
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.get(f"/api/cases/{case['id']}/deletion")).status_code == 404
    assert (await client.delete(f"/api/cases/{case['id']}")).status_code == 404
    client.cookies.clear()

    await login(client, "anna@example.com")
    assert (await client.get(f"/api/cases/{case['id']}")).status_code == 200


async def test_a_deleted_case_leaves_its_group(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    codes = await countries(client)

    async def family(*names: str) -> list[dict]:
        response = await client.post(
            f"{CONTACTS}/{anna['id']}/cases",
            params={"allow_case_in_work": "true"},  # her own case is in work already
            json={
                "destination_id": codes["PL"],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": anna["id"]}]
                + [{"first_name": name} for name in names],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    # Three in a group: the other two stay together.
    first, second, third = await family("Сергей", "Мария")
    assert (await client.get(f"/api/cases/{third['id']}/deletion")).json()["group_members"] == 2
    assert (await client.delete(f"/api/cases/{third['id']}")).status_code == 204
    left = (await client.get(f"/api/cases/{first['id']}")).json()
    assert left["group_id"] == first["group_id"]
    assert [m["case_id"] for m in left["group"]] == [second["id"]]

    # Two in a group: the one left is alone, the group is gone.
    one, two = await family("Пётр")
    assert (await client.delete(f"/api/cases/{two['id']}")).status_code == 204
    assert (await client.get(f"/api/cases/{one['id']}")).json()["group_id"] is None


async def test_a_number_is_never_given_again(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    first = await one_case(client, anna["id"])
    second = await one_case(client, anna["id"])
    assert (first["number"], second["number"]) == (1, 2)
    assert (await client.delete(f"/api/cases/{second['id']}")).status_code == 204
    assert (await one_case(client, anna["id"]))["number"] == 3


async def test_an_erased_workspace_numbers_its_cases_from_one(
    client: AsyncClient, make_user: MakeUser
):
    """Erasing the workspace starts it again like a new one: the kept number goes too."""
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    await one_case(client, anna["id"])
    second = await one_case(client, anna["id"])
    assert (await client.delete(f"/api/cases/{second['id']}")).status_code == 204

    erased = await client.post(
        "/api/workspace/erase", json={"password": PASSWORD, "code": CLOCK.next_code()}
    )
    assert erased.status_code == 204, erased.text
    bella = await new_contact(client, first_name="Белла")
    assert (await one_case(client, bella["id"]))["number"] == 1


async def test_an_import_numbers_past_a_deleted_case(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    anna = await new_contact(client)
    mistake = await one_case(client, anna["id"])
    assert (await client.delete(f"/api/cases/{mistake['id']}")).status_code == 204

    await run(client, {"Клиенты": IMPORTED_CLIENTS, "Кейсы": IMPORTED_CASES})
    async with owner_engine.connect() as conn:
        numbers = (
            await conn.scalars(
                text("SELECT number FROM visa_cases WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).all()
    assert numbers and min(numbers) == 2


async def test_a_deleted_card_takes_its_cases_out_of_their_groups(
    client: AsyncClient, make_user: MakeUser
):
    """Owner's decision 2026-11-24: a card goes with its cases. Each of them leaves its
    family group as a deleted case does, is logged as deleted, and its number is not given
    again."""
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    codes = await countries(client)
    family = await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": anna["id"]}, {"first_name": "Сергей"}],
        },
    )
    assert family.status_code == 201, family.text
    hers, his = family.json()
    # Her newest case has the highest number: it must not be given again.
    france = await one_case(client, anna["id"], "FR")

    summary = (await client.get(f"{CONTACTS}/{anna['id']}/deletion")).json()
    assert summary["cases"] == [hers["number"], france["number"]]
    assert (await client.delete(f"{CONTACTS}/{anna['id']}")).status_code == 204

    left = (await client.get(f"/api/cases/{his['id']}")).json()
    assert (left["group_id"], left["group"]) == (None, [])
    events = (await client.get("/api/audit")).json()["events"]
    deleted = {e["target_id"] for e in events if e["action"] == "cases.deleted"}
    assert deleted == {hers["id"], france["id"]}
    assert (await one_case(client, his["contact_id"], "FR"))["number"] == france["number"] + 1

"""Deleting a case opened by mistake — a duplicate (pilot fix 1, docs/decisions.md →
«Пилот: правки визовика (2026-11-19)», T1): the specialist only; a case with money stays;
everything else of the case goes with it; its number is never given again; a family
group is rebuilt without it."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import CLOCK, PASSWORD, MakeUser, give_cases, login, workspace_today
from tests.test_cases import CONTACTS, as_specialist, countries, new_contact, open_case
from tests.test_data_import import CASES as IMPORTED_CASES
from tests.test_data_import import CLIENTS as IMPORTED_CLIENTS
from tests.test_data_import import run
from tests.test_employee_cases import EMPLOYEE, switch_to, with_employee


async def one_case(client: AsyncClient, contact_id: str, destination="PL") -> dict:
    response = await open_case(client, contact_id, destination)
    assert response.status_code == 201, response.text
    return response.json()[0]


async def rows_of(owner_engine: AsyncEngine, case_id: str) -> dict[str, int]:
    tables = (
        "application_attempts",
        "waiting_list_entries",
        "package_items",
        "bookings",
        "case_finances",
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
    assert summary["has_money"] is False

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    cases = (await client.get(f"{CONTACTS}/{naumik['id']}")).json()["cases"]
    assert [c["id"] for c in cases] == [kept["id"]]
    board = (await client.get("/api/cases/board")).json()["cases"]
    assert duplicate["id"] not in {c["id"] for c in board}

    events = (await client.get("/api/audit")).json()["events"]
    deleted = [e for e in events if e["action"] == "cases.deleted"]
    assert [e["target_id"] for e in deleted] == [duplicate["id"]]
    assert deleted[0]["details"]["contact"] == naumik["id"]
    assert "Naumik" not in str(deleted[0]["details"])


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
        "has_money": False,
        "queue_entries": 1,
        "package_items": 1,
        "bookings": 1,
        "outcome": True,
        "employees": 1,
    }
    assert (await client.get(f"/api/cases/{france['id']}/deletion")).json()["attempts"] == 1

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


async def test_a_case_with_money_is_not_deleted(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    paid, spent, talon = [await one_case(client, anna["id"]) for _ in range(3)]
    payment = await client.post(
        f"/api/cases/{paid['id']}/payments",
        json={"paid_on": "2026-05-15", "amount": "150", "currency": "BYN"},
    )
    assert payment.status_code == 201, payment.text
    expense = await client.post(
        f"/api/cases/{spent['id']}/expenses",
        json={"spent_on": "2026-05-15", "amount": "10", "currency": "BYN", "description": "Фото"},
    )
    assert expense.status_code == 201, expense.text
    fee = await client.patch(
        f"/api/cases/{talon['id']}/finance",
        json={"registrar_fee_amount": "60", "registrar_fee_currency": "BYN"},
    )
    assert fee.status_code == 200, fee.text

    for case, field in ((paid, "payments"), (spent, "expenses"), (talon, "registrar_fee")):
        summary = (await client.get(f"/api/cases/{case['id']}/deletion")).json()
        assert summary["has_money"] is True and summary[field] in (1, True)
        refused = await client.delete(f"/api/cases/{case['id']}")
        assert refused.status_code == 409 and refused.json()["detail"] == "case_has_money"
        assert (await client.get(f"/api/cases/{case['id']}")).status_code == 200

    # The payment deleted first, the case goes.
    [row] = (await client.get(f"/api/cases/{paid['id']}/finance")).json()["payments"]
    assert (await client.delete(f"/api/payments/{row['id']}")).status_code in (200, 204)
    assert (await client.delete(f"/api/cases/{paid['id']}")).status_code == 204


async def test_a_case_whose_partner_was_paid_is_not_deleted(
    client: AsyncClient, make_user: MakeUser
):
    """Owner's answer 2026-11-20: a partner payout marked as paid holds the case like a
    payment does; agreed terms without the mark do not."""
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    case = await one_case(client, anna["id"])
    url = f"/api/cases/{case['id']}"
    terms = {"price": "500", "partner_terms": "fixed", "partner_fixed": "50"}
    assert (await client.patch(f"{url}/finance", json=terms)).status_code == 200
    summary = (await client.get(f"{url}/deletion")).json()
    assert (summary["partner_paid"], summary["has_money"]) == (False, False)

    paid = await client.patch(f"{url}/finance", json={"partner_paid_on": "2026-06-01"})
    assert paid.status_code == 200, paid.text
    summary = (await client.get(f"{url}/deletion")).json()
    assert (summary["partner_paid"], summary["has_money"]) == (True, True)
    refused = await client.delete(url)
    assert refused.status_code == 409 and refused.json()["detail"] == "case_has_money"
    assert (await client.get(url)).status_code == 200

    # The mark taken off, the case goes.
    unpaid = await client.patch(f"{url}/finance", json={"partner_paid_on": None})
    assert unpaid.status_code == 200, unpaid.text
    assert (await client.delete(url)).status_code == 204


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

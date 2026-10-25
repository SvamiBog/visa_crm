"""Bookings of a case and the «Брони: дедлайны» view (backlog 1.8)."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login, workspace_today


def day(offset: int) -> str:
    return (workspace_today() + timedelta(days=offset)).isoformat()


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def open_case(client: AsyncClient, name: str = "Анна", destination: str = "PL") -> dict:
    contact = (await client.post("/api/contacts", json={"first_name": name})).json()
    codes = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    response = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": codes[destination],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": contact["id"]}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


async def add(client: AsyncClient, case_id: str, **body):
    return await client.post(f"/api/cases/{case_id}/bookings", json=body)


async def hotel(client: AsyncClient, case_id: str, **body) -> dict:
    response = await add(
        client,
        case_id,
        **{"kind": "hotel", "place": "Отель, Краков", "starts_on": day(40), "ends_on": day(44)}
        | body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def card_case(client: AsyncClient, case: dict) -> dict:
    return (await client.get(f"/api/cases/{case['id']}")).json()


async def test_hotel_booking_with_its_cancellation_deadline(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    case = await open_case(client)
    booking = await hotel(client, case["id"], reference=" 4521 ", free_cancel_until=day(3))
    assert booking["status"] == "active"
    assert booking["reference"] == "4521"
    assert (booking["deadline"], booking["days_left"]) == (day(3), 3)
    assert booking["warnings"] == []

    await hotel(client, case["id"], place="Отель, Варшава", free_cancel_until=day(8))
    listed = (await card_case(client, case))["bookings"]
    assert [b["place"] for b in listed] == ["Отель, Краков", "Отель, Варшава"]

    # A cancelled booking goes after the live ones and needs nothing.
    cancelled = await client.patch(f"/api/bookings/{booking['id']}", json={"status": "cancelled"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    listed = (await card_case(client, case))["bookings"]
    assert [b["status"] for b in listed] == ["active", "cancelled"]

    event = (await client.get("/api/audit")).json()["events"][0]
    assert event["action"] == "cases.booking_updated"
    # Ids and field names only: no hotel, dates or booking number.
    assert event["details"] == {
        "case": case["id"],
        "contact": case["contact_id"],
        "fields": "status",
    }


async def test_dates_must_fit_the_kind_of_booking(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    case = await open_case(client)
    wrong = await add(client, case["id"], kind="hotel", place="X", valid_until=day(5))
    assert wrong.status_code == 422 and wrong.json()["detail"] == "booking_dates_kind"
    wrong = await add(client, case["id"], kind="flight", place="X", free_cancel_until=day(5))
    assert wrong.status_code == 422 and wrong.json()["detail"] == "booking_dates_kind"
    backwards = await add(
        client, case["id"], kind="hotel", place="X", starts_on=day(5), ends_on=day(4)
    )
    assert backwards.status_code == 422 and backwards.json()["detail"] == "dates_order"
    blank = await add(client, case["id"], kind="hotel", place="  ")
    assert blank.status_code == 422


async def test_flight_booking_that_ends_before_the_submission_is_flagged(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    case = await open_case(client)
    await client.patch(f"/api/cases/{case['id']}", json={"submission_on": day(30)})
    response = await add(
        client, case["id"], kind="flight", place="Минск — Варшава", valid_until=day(20)
    )
    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == ["expires_before_submission"]
    fixed = await client.patch(
        f"/api/bookings/{response.json()['id']}", json={"valid_until": day(35)}
    )
    assert fixed.json()["warnings"] == []


async def test_closing_a_case_shows_its_live_bookings(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    case = await open_case(client)
    await hotel(client, case["id"], free_cancel_until=day(5))
    closed = await client.patch(
        f"/api/cases/{case['id']}", json={"outcome": "visa", "decided_on": day(0)}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["stage"] == "result"
    assert closed.json()["bookings"][0]["warnings"] == ["case_closed"]


async def test_deadlines_view_lists_live_bookings_by_the_nearest_deadline(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    anna = await open_case(client)
    boris = await open_case(client, "Борис", destination="FR")
    later = await hotel(client, anna["id"], place="Поздняя", free_cancel_until=day(9))
    await hotel(client, boris["id"], place="Ранняя", free_cancel_until=day(2))
    await hotel(client, anna["id"], place="Без даты")
    gone = await hotel(client, anna["id"], place="Отменена", free_cancel_until=day(1))
    await client.patch(f"/api/bookings/{gone['id']}", json={"status": "cancelled"})
    await hotel(client, anna["id"], place="Прошла", starts_on=day(-10), ends_on=day(-5))
    moved = await client.patch(f"/api/bookings/{later['id']}", json={"status": "rescheduled"})
    assert moved.status_code == 200

    rows = (await client.get("/api/bookings/deadlines")).json()
    assert [r["place"] for r in rows] == ["Ранняя", "Поздняя", "Без даты"]
    first = rows[0]
    assert (first["contact_name"], first["case_number"], first["days_left"]) == ("Борис", 2, 2)
    assert first["stage"] == "waiting"

    france = boris["destination_id"]
    only = (await client.get(f"/api/bookings/deadlines?country_id={france}")).json()
    assert [r["place"] for r in only] == ["Ранняя"]


async def test_booking_account_is_the_workspaces_or_the_clients(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    case = await open_case(client)
    other = await open_case(client, "Борис")
    system = (
        await client.post(
            "/api/dictionaries/vault-systems", json={"name": "Booking.com", "kind": "booking"}
        )
    ).json()
    own = (
        await client.post(
            "/api/vault/entries", json={"system_id": system["id"], "login": "b2@mail.by"}
        )
    ).json()
    borises = (
        await client.post(
            "/api/vault/entries",
            json={
                "system_id": system["id"],
                "contact_id": other["contact_id"],
                "login": "boris@mail.by",
            },
        )
    ).json()
    booking = await hotel(client, case["id"], vault_entry_id=own["id"])
    assert booking["vault_entry_id"] == own["id"]
    wrong = await client.patch(
        f"/api/bookings/{booking['id']}", json={"vault_entry_id": borises["id"]}
    )
    assert wrong.status_code == 422 and wrong.json()["detail"] == "account_of_another_contact"

    # The account is deleted from the vault: the booking stays, without it.
    await client.delete(f"/api/vault/entries/{own['id']}")
    assert (await card_case(client, case))["bookings"][0]["vault_entry_id"] is None


async def test_bookings_need_the_cases_module_and_stay_in_the_workspace(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    case = await open_case(client)
    booking = await hotel(client, case["id"], free_cancel_until=day(4))

    client.cookies.clear()
    await as_specialist(client, make_user, "boris@example.com")
    assert (
        await client.patch(f"/api/bookings/{booking['id']}", json={"note": "x"})
    ).status_code == 404
    assert (await add(client, case["id"], kind="hotel", place="X")).status_code == 404
    assert (await client.get("/api/bookings/deadlines")).json() == []

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    denied = await client.get("/api/bookings/deadlines")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    client.cookies.clear()
    await login(client, "anna@example.com")
    deleted = await client.delete(f"/api/bookings/{booking['id']}")
    assert deleted.status_code == 204
    assert (await card_case(client, case))["bookings"] == []

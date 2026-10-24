"""«Требует внимания» (backlog 1.11): reminders computed from clients, cases, bookings and
money; «Готово» hides one occurrence; the windows are the specialist's settings."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

TODAY = datetime.now(ZoneInfo("Europe/Minsk")).date()


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str, **fields) -> dict:
    response = await client.post("/api/contacts", json={"first_name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, codes: dict, person: dict, country: str, **case) -> dict:
    response = await client.post(
        f"/api/contacts/{person['id']}/cases",
        json={
            "destination_id": codes[country],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": person["id"]}],
        }
        | case,
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


async def patch_case(client: AsyncClient, case: dict, **body) -> dict:
    response = await client.patch(f"/api/cases/{case['id']}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def workspace_with_everything(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> dict:
    """One client or registrar for every kind of reminder; returns them by name."""
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    codes = await countries(client)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE country_rules SET appeals_accepted = true, appeal_deadline_days = 14 "
                "WHERE destination_id = :lt AND submission_id = :by"
            ),
            {"lt": codes["LT"], "by": codes["BY"]},
        )

    birthday = (
        TODAY.replace(year=1990) if (TODAY.month, TODAY.day) != (2, 29) else date(1992, 2, 29)
    )
    anna = await contact(client, "Анна", birth_date=birthday.isoformat())
    poland = await open_case(client, codes, anna, "PL")
    await patch_case(client, poland, submission_on=day(2))
    await client.patch(f"/api/cases/{poland['id']}/finance", json={"price": "500"})
    hotel = await client.post(
        f"/api/cases/{poland['id']}/bookings",
        json={
            "kind": "hotel",
            "place": "Отель, Краков",
            "starts_on": day(10),
            "ends_on": day(14),
            "free_cancel_until": day(1),
        },
    )
    assert hotel.status_code == 201, hotel.text

    boris = await contact(client, "Борис")
    thinking = await client.post(
        f"/api/contacts/{boris['id']}/consultations",
        json={"held_on": day(-40), "decision": "thinking"},
    )
    assert thinking.status_code == 201, thinking.text

    vera = await contact(client, "Вера")
    france = await open_case(client, codes, vera, "FR")
    sent = await client.post(f"/api/cases/{france['id']}/attempts", json={"sent_on": day(-57)})
    assert sent.status_code == 201, sent.text

    gleb = await contact(client, "Глеб")
    latvia = await open_case(client, codes, gleb, "LV")
    await patch_case(
        client,
        latvia,
        outcome="visa",
        decided_on=day(-100),
        visa_entries="multiple",
        visa_valid_from=day(-90),
        visa_valid_to=day(30),
    )

    dasha = await contact(client, "Даша")
    lithuania = await open_case(client, codes, dasha, "LT")
    await patch_case(client, lithuania, outcome="refusal", decided_on=day(-2))

    registrar = await client.post(
        "/api/dictionaries/registrars",
        json={"name": "Иван", "kind": "individual", "currency": "EUR", "low_balance": "100"},
    )
    assert registrar.status_code == 201, registrar.text
    return {"tenant_id": tenant_id, "anna": anna, "boris": boris, "poland": poland}


def by_kind(items: list[dict]) -> dict[str, dict]:
    return {item["kind"]: item for item in items}


async def test_the_attention_list(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    made = await workspace_with_everything(client, make_user, owner_engine)
    items = (await client.get("/api/reminders")).json()
    found = by_kind(items)
    assert set(found) == {
        "birthday",
        "submission",
        "balance_due",
        "application_deadline",
        "booking_deadline",
        "thinking",
        "visa_expires",
        "appeal",
        "registrar_advance",
    }
    assert (found["submission"]["due_on"], found["submission"]["days_left"]) == (day(2), 2)
    assert found["balance_due"]["amount"] == "500.00"
    assert (found["application_deadline"]["due_on"], found["booking_deadline"]["due_on"]) == (
        day(3),
        day(1),
    )
    assert found["appeal"]["due_on"] == day(12)
    assert (found["visa_expires"]["contact_name"], found["thinking"]["contact_name"]) == (
        "Глеб",
        "Борис",
    )
    advance = found["registrar_advance"]
    assert (advance["key"], advance["amount"], advance["currency"]) == (None, "0.00", "EUR")
    # Overdue and nearest first; the registrar's advance has no date and goes last.
    dated = [i["days_left"] for i in items if i["days_left"] is not None]
    assert dated == sorted(dated) and items[-1]["kind"] == "registrar_advance"
    assert found["thinking"]["since"] == day(-40)
    # Keys name ids and days only.
    assert found["submission"]["key"] == f"submission:{made['poland']['id']}:{day(-1)}"

    # «Готово» hides that occurrence for the whole workspace.
    done = await client.post("/api/reminders/dismiss", json={"key": found["submission"]["key"]})
    assert done.status_code == 204
    left = by_kind((await client.get("/api/reminders")).json())
    assert "submission" not in left and "balance_due" in left
    again = await client.post("/api/reminders/dismiss", json={"key": found["submission"]["key"]})
    assert again.status_code == 404  # no longer shown
    bad = await client.post("/api/reminders/dismiss", json={"key": "submission:1:2026-01-01"})
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_reminder"

    # In the client card: that client's reminders, without the registrars.
    card = (await client.get(f"/api/reminders?contact_id={made['anna']['id']}")).json()
    assert {i["kind"] for i in card} == {"birthday", "balance_due", "booking_deadline"}


async def test_the_windows_are_the_specialists(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await workspace_with_everything(client, make_user, owner_engine)
    defaults = (await client.get("/api/reminders/settings")).json()
    assert defaults == {
        "submission_days": 3,
        "balance_days": 3,
        "application_days": 5,
        "booking_days": 3,
        "thinking_months": [1, 3],
        "visa_expiry_months": 2,
        "appeal_days": 3,
    }
    saved = await client.put(
        "/api/reminders/settings",
        json=defaults | {"submission_days": 1, "thinking_months": [3, 2, 3]},
    )
    assert saved.status_code == 200 and saved.json()["thinking_months"] == [2, 3]
    kinds = {i["kind"] for i in (await client.get("/api/reminders")).json()}
    assert "submission" not in kinds and "thinking" not in kinds  # 2 days ahead; 40 days ago
    too_wide = await client.put("/api/reminders/settings", json=defaults | {"booking_days": 0})
    assert too_wide.status_code == 422
    events = (await client.get("/api/audit")).json()["events"]
    assert any(e["action"] == "reminders.settings_updated" for e in events)


async def test_reminders_follow_modules_and_workspaces(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    made = await workspace_with_everything(client, make_user, owner_engine)
    key = by_kind((await client.get("/api/reminders")).json())["birthday"]["key"]

    # An employee without money sees no balances and no registrars, and sets no windows.
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=made["tenant_id"])
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET modules = '{clients,cases,tasks}' "
                "WHERE email = 'olga@example.com'"
            )
        )
    await login(client, "olga@example.com")
    kinds = {i["kind"] for i in (await client.get("/api/reminders")).json()}
    assert "birthday" in kinds and not kinds & {"balance_due", "registrar_advance"}
    settings = (await client.get("/api/reminders/settings")).json()
    assert (await client.put("/api/reminders/settings", json=settings)).status_code == 403

    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    denied = await client.get("/api/reminders")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    # Another workspace sees none of it and cannot dismiss it.
    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    assert (await client.get("/api/reminders")).json() == []
    stray = await client.post("/api/reminders/dismiss", json={"key": key})
    assert stray.status_code == 404

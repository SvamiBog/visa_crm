"""The passport's data goes a week after the decision (backlog 2.8): «Требует внимания» shows
it two days before with «Отложить на 30 дней» and «Удалить сейчас», the daily run removes
it, and the first run after the release removes nothing at once."""

import importlib.util
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import passport_purge
from app.core.config import get_settings
from tests.support.helpers import MakeUser, give_cases, login, workspace_today

PASSPORT = {
    "passport_number": "MP1234567",
    "passport_issued_on": "2021-03-04",
    "passport_expires_on": "2031-03-03",
    "passport_issued_by": "МВД",
    "nationality": "BY",
    "sex": "F",
    "personal_number": "3120585A001PB8",
    "birth_place": "г. Минск",
}
ALL_FIELDS = list(passport_purge.PURGED_FIELDS)


def day(n: int) -> str:
    return (workspace_today() + timedelta(days=n)).isoformat()


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def client_with_passport(client: AsyncClient, name: str = "Анна") -> dict:
    response = await client.post(
        "/api/contacts",
        json={
            "first_name": name,
            "last_name_latin": "KOVALEVA",
            "first_name_latin": "ANNA",
            "birth_date": "1990-05-12",
            "phone": f"+37529{abs(hash(name)) % 10_000_000:07d}",
            "passport": PASSPORT,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, person: dict, country: str = "PL") -> dict:
    codes = await countries(client)
    response = await client.post(
        f"/api/contacts/{person['id']}/cases",
        json={
            "destination_id": codes[country],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": person["id"]}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


async def decide(client: AsyncClient, case: dict, decided_on: str | None, outcome="refusal"):
    body = {"stage": "result", "outcome": outcome, "decided_on": decided_on}
    response = await client.patch(f"/api/cases/{case['id']}", json=body)
    assert response.status_code == 200, response.text


async def decided_client(client: AsyncClient, decided_days_ago: int, name: str = "Анна") -> dict:
    person = await client_with_passport(client, name)
    case = await open_case(client, person)
    await decide(client, case, day(-decided_days_ago))
    return person | {"case_id": case["id"]}


async def reminders(client: AsyncClient) -> list[dict]:
    response = await client.get("/api/reminders")
    assert response.status_code == 200, response.text
    return [r for r in response.json() if r["kind"] == "passport_purge"]


async def questionnaire(client: AsyncClient, person: dict) -> dict:
    return (await client.get(f"/api/contacts/{person['id']}")).json()["questionnaire"]


async def audit(owner_engine: AsyncEngine, action: str) -> list[dict]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT actor_id, target_id, details FROM audit_events "
                "WHERE action = :action ORDER BY id"
            ),
            {"action": action},
        )
        return [dict(r._mapping) for r in rows]


async def entered_days_ago(owner_engine: AsyncEngine, case_id: str, days: int) -> None:
    """The result was entered in the CRM `days` ago (the trigger otherwise says now)."""
    async with owner_engine.begin() as conn:
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(
            text(
                "UPDATE visa_cases SET decision_entered_at = now() - make_interval(days => :d) "
                "WHERE id = :id"
            ),
            {"d": days, "id": case_id},
        )


# --- «Требует внимания» -------------------------------------------------------------


async def test_two_days_before_the_removal_the_client_is_listed_with_what_goes(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    # Decided 6 days ago, entered today: goes in two days (never sooner after entering).
    soon = await decided_client(client, 6, "Анна")
    await decided_client(client, 1, "Борис")  # goes in six days: not yet listed

    found = await reminders(client)
    assert [r["contact_id"] for r in found] == [soon["id"]]
    item = found[0]
    assert (item["due_on"], item["days_left"], item["key"]) == (day(2), 2, None)
    assert item["passport_fields"] == ALL_FIELDS
    assert item["contact_name"] == soon["full_name"]


async def test_a_case_in_work_keeps_the_passport(client: AsyncClient, make_user: MakeUser):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 10)
    await open_case(client, anna, "LT")  # another trip, still waiting

    assert await reminders(client) == []
    for action in ("postpone", "purge"):
        refused = await client.post(f"/api/reminders/passport/{anna['id']}/{action}")
        assert refused.status_code == 409
        assert refused.json()["detail"] == "passport_purge_not_due"


async def test_a_client_without_passport_data_is_not_listed(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    response = await client.post("/api/contacts", json={"first_name": "Вера"})
    vera = response.json()
    await decide(client, await open_case(client, vera), day(-10))
    assert await reminders(client) == []


# --- «Отложить на 30 дней» и «Удалить сейчас» ----------------------------------------


async def test_put_off_for_30_days_and_again(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 6)

    first = await client.post(f"/api/reminders/passport/{anna['id']}/postpone")
    assert first.status_code == 200, first.text
    assert first.json() == {"purge_on": day(32)}
    assert await reminders(client) == []
    # Again, for an appeal: 30 more days from the new day.
    again = await client.post(f"/api/reminders/passport/{anna['id']}/postpone")
    assert again.json() == {"purge_on": day(62)}

    assert (await questionnaire(client, anna))["passport_number"] == "MP1234567"
    events = await audit(owner_engine, "contacts.passport_purge_postponed")
    assert [(e["target_id"], e["details"]) for e in events] == [
        (anna["id"], {"until": day(32)}),
        (anna["id"], {"until": day(62)}),
    ]
    assert all(e["actor_id"] is not None for e in events)
    # The daily run leaves a postponed passport alone.
    assert await passport_purge.purge_due() == 0


async def test_remove_now_takes_the_passport_and_keeps_the_rest(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 6)
    answers = {**(await questionnaire(client, anna)), "deportation": True}
    assert (await client.put(f"/api/contacts/{anna['id']}/questionnaire", json=answers)).is_success

    removed = await client.post(f"/api/reminders/passport/{anna['id']}/purge")
    assert removed.status_code == 204, removed.text

    found = (await client.get(f"/api/contacts/{anna['id']}")).json()
    q, card = found["questionnaire"], found["contact"]
    assert all(q[field] is None for field in ALL_FIELDS)
    assert (q["nationality"], q["sex"], q["deportation"]) == ("BY", "F", True)
    assert (card["birth_date"], card["phone"], card["full_name_latin"]) == (
        "1990-05-12",
        anna["phone"],
        "KOVALEVA ANNA",
    )
    assert card["phone"]
    case = (await client.get(f"/api/cases/{anna['case_id']}")).json()
    assert (case["outcome"], case["decided_on"]) == ("refusal", day(-6))
    assert await reminders(client) == []

    events = await audit(owner_engine, "contacts.passport_purged_manually")
    assert [(e["target_id"], e["details"]) for e in events] == [
        (anna["id"], {"fields": ALL_FIELDS, "files": 0})
    ]
    # Nothing is left to remove.
    again = await client.post(f"/api/reminders/passport/{anna['id']}/purge")
    assert again.status_code == 409


async def test_another_specialist_cannot_put_off_or_remove(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 6)

    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    for action in ("postpone", "purge"):
        refused = await client.post(f"/api/reminders/passport/{anna['id']}/{action}")
        assert refused.status_code == 404
    assert await reminders(client) == []
    unknown = await client.post(f"/api/reminders/passport/{uuid.uuid4()}/purge")
    assert unknown.status_code == 404

    client.cookies.clear()
    await login(client, "anna@example.com")
    assert (await questionnaire(client, anna))["passport_number"] == "MP1234567"
    assert [r["contact_id"] for r in await reminders(client)] == [anna["id"]]


async def test_an_employee_acts_only_on_clients_of_the_cases_given_to_them(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    given = await decided_client(client, 6, "Анна")
    other = await decided_client(client, 6, "Борис")
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases,tasks}' WHERE email = :e"),
            {"e": "olga@example.com"},
        )
    await give_cases(owner_engine, "olga@example.com", given["case_id"])

    client.cookies.clear()
    await login(client, "olga@example.com")
    assert [r["contact_id"] for r in await reminders(client)] == [given["id"]]
    hidden = await client.post(f"/api/reminders/passport/{other['id']}/purge")
    assert hidden.status_code == 404
    done = await client.post(f"/api/reminders/passport/{given['id']}/postpone")
    assert done.status_code == 200, done.text
    removed = await client.post(f"/api/reminders/passport/{given['id']}/purge")
    assert removed.status_code == 204

    client.cookies.clear()
    await login(client, "anna@example.com")
    assert (await questionnaire(client, given))["passport_number"] is None
    assert (await questionnaire(client, other))["passport_number"] == "MP1234567"


async def test_a_case_in_work_hidden_from_the_employee_still_keeps_the_passport(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 10)
    await open_case(client, anna, "LT")  # in work, not given to the employee
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases,tasks}' WHERE email = :e"),
            {"e": "olga@example.com"},
        )
    await give_cases(owner_engine, "olga@example.com", anna["case_id"])

    client.cookies.clear()
    await login(client, "olga@example.com")
    assert await reminders(client) == []
    refused = await client.post(f"/api/reminders/passport/{anna['id']}/purge")
    assert refused.status_code == 409


# --- The daily run -------------------------------------------------------------------


async def test_the_daily_run_removes_what_is_due_and_only_that(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    due = await decided_client(client, 7, "Анна")
    await entered_days_ago(owner_engine, due["case_id"], 7)
    tomorrow = await decided_client(client, 6, "Борис")
    await entered_days_ago(owner_engine, tomorrow["case_id"], 6)
    # Decided long ago but entered today: two days of notice first.
    late = await decided_client(client, 30, "Вера")

    assert await passport_purge.purge_due() == 1
    assert (await questionnaire(client, due))["passport_number"] is None
    assert (await questionnaire(client, tomorrow))["passport_number"] == "MP1234567"
    assert (await questionnaire(client, late))["passport_number"] == "MP1234567"
    late_item = next(r for r in await reminders(client) if r["contact_id"] == late["id"])
    assert late_item["due_on"] == day(2)

    events = await audit(owner_engine, "contacts.passport_purged")
    assert [(e["actor_id"], e["target_id"]) for e in events] == [(None, due["id"])]
    # Run again: nothing more.
    assert await passport_purge.purge_due() == 0


async def test_a_result_without_a_date_counts_from_the_day_it_was_entered(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await client_with_passport(client)
    case = await open_case(client, anna)
    await decide(client, case, None, outcome="not_submitted")
    await entered_days_ago(owner_engine, case["id"], 6)
    assert [r["due_on"] for r in await reminders(client)] == [day(1)]
    await entered_days_ago(owner_engine, case["id"], 7)
    assert await passport_purge.purge_due() == 1


async def test_the_day_entered_follows_the_result_and_its_date(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    anna = await decided_client(client, 6)

    async def entered():
        async with owner_engine.connect() as conn:
            return await conn.scalar(
                text("SELECT decision_entered_at FROM visa_cases WHERE id = :id"),
                {"id": anna["case_id"]},
            )

    assert await entered() is not None
    await entered_days_ago(owner_engine, anna["case_id"], 5)
    before = await entered()
    # Another field of the case changes nothing; a new date of decision does.
    await client.patch(f"/api/cases/{anna['case_id']}", json={"vac_number": "PL123"})
    assert await entered() == before
    await client.patch(f"/api/cases/{anna['case_id']}", json={"decided_on": day(-3)})
    assert await entered() > before


# --- The first run after the release -------------------------------------------------


def _first_run_sql() -> str:
    path = Path(__file__).parents[1] / "migrations" / "versions" / "0046_passport_purge.py"
    spec = importlib.util.spec_from_file_location("migration_0046", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FIRST_RUN


async def test_the_first_run_removes_nothing_and_lists_everything_due(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    old = await decided_client(client, 60, "Анна")
    await entered_days_ago(owner_engine, old["case_id"], 60)
    in_work = await decided_client(client, 60, "Борис")
    await entered_days_ago(owner_engine, in_work["case_id"], 60)
    await open_case(client, in_work, "LT")

    async with owner_engine.begin() as conn:
        await conn.execute(text(_first_run_sql()))

    assert await passport_purge.purge_due() == 0
    listed = await reminders(client)
    assert [(r["contact_id"], r["due_on"]) for r in listed] == [(old["id"], day(14))]
    assert (await questionnaire(client, old))["passport_number"] == "MP1234567"


# --- Production: off until PASSPORT_PURGE_FROM ------------------------------------------

Production = Callable[[date | None], None]


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> Production:
    """The removal sees production settings with PASSPORT_PURGE_FROM as given; the rest of
    the app stays a test site (cookies, docs)."""

    def switch(switched_on_from: date | None) -> None:
        settings = get_settings().model_copy(
            update={"env": "production", "passport_purge_from": switched_on_from}
        )
        monkeypatch.setattr(passport_purge, "get_settings", lambda: settings)

    return switch


async def test_in_production_nothing_is_listed_removed_or_answered_until_switched_on(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, production: Production
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    due = await decided_client(client, 30, "Анна")
    await entered_days_ago(owner_engine, due["case_id"], 30)
    soon = await decided_client(client, 6, "Борис")
    # The release's first run listed someone too.
    async with owner_engine.begin() as conn:
        await conn.execute(text(_first_run_sql()))

    # Switched on from tomorrow: still off today.
    for switched_on_from in (None, workspace_today() + timedelta(days=1)):
        production(switched_on_from)
        assert await reminders(client) == []
        assert await passport_purge.purge_due() == 0
        for person in (due, soon):
            for action in ("postpone", "purge"):
                response = await client.post(f"/api/reminders/passport/{person['id']}/{action}")
                assert response.status_code == 404, response.text
                assert response.json()["detail"] == "not_found"
        assert (await questionnaire(client, due))["passport_number"] == "MP1234567"
    assert await audit(owner_engine, "contacts.passport_purged") == []
    assert await audit(owner_engine, "contacts.passport_purge_postponed") == []


async def test_switched_on_later_what_is_due_waits_fourteen_days_from_that_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, production: Production
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    # Due long ago, and kept by the release's first run until a day now past (in
    # production: kept until 08.12.2026, the switch comes after it).
    kept = await decided_client(client, 60, "Анна")
    await entered_days_ago(owner_engine, kept["case_id"], 60)
    async with owner_engine.begin() as conn:
        await conn.execute(text(_first_run_sql()))
        await conn.execute(
            text(
                "UPDATE contacts SET passport_kept_until = :kept, passport_notice_from = :notice "
                "WHERE id = :id"
            ),
            {
                "kept": workspace_today() - timedelta(days=3),
                "notice": workspace_today() - timedelta(days=17),
                "id": kept["id"],
            },
        )
    # Due long ago, never listed.
    old = await decided_client(client, 40, "Борис")
    await entered_days_ago(owner_engine, old["case_id"], 40)
    # Put off until twenty days from now: its own day stays.
    later = await decided_client(client, 1, "Вера")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE contacts SET passport_kept_until = :kept WHERE id = :id"),
            {"kept": workspace_today() + timedelta(days=20), "id": later["id"]},
        )

    production(workspace_today())
    assert await passport_purge.purge_due() == 0
    listed = {r["contact_id"]: r["due_on"] for r in await reminders(client)}
    assert listed == {kept["id"]: day(14), old["id"]: day(14)}
    assert (await questionnaire(client, kept))["passport_number"] == "MP1234567"

    # Fourteen days after the switch: those two go, the later one waits for its day.
    production(workspace_today() - timedelta(days=14))
    assert await passport_purge.purge_due() == 2
    assert (await questionnaire(client, kept))["passport_number"] is None
    assert (await questionnaire(client, old))["passport_number"] is None
    assert (await questionnaire(client, later))["passport_number"] == "MP1234567"


async def test_switched_on_the_buttons_work_as_on_the_stands(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, production: Production
):
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    person = await decided_client(client, 30, "Анна")
    await entered_days_ago(owner_engine, person["case_id"], 30)
    production(workspace_today())

    response = await client.post(f"/api/reminders/passport/{person['id']}/postpone")
    assert response.status_code == 200, response.text
    assert response.json() == {"purge_on": day(44)}
    response = await client.post(f"/api/reminders/passport/{person['id']}/purge")
    assert response.status_code == 204, response.text
    assert (await questionnaire(client, person))["passport_number"] is None

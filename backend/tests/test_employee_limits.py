"""Daily limits of an employee (backlog Т.14): what they open goes to the log, past the
limit they are paused until the next day, and the specialist sees it in «Требует
внимания». Rules: tests/test_employee_limit_rules.py."""

import asyncio
from collections.abc import Awaitable
from datetime import timedelta

from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import AuditEvent
from tests.helpers import WALL_CLOCK, MakeUser, give_cases
from tests.test_cases import CONTACTS, countries, new_contact, open_case
from tests.test_documents import upload as upload_document
from tests.test_employee_cases import EMPLOYEE, switch_to, user_id, with_employee
from tests.test_templates import turn_on
from tests.test_templates import upload as upload_template
from tests.test_vault import entry, reveal, system


async def events(owner_engine: AsyncEngine, action: str) -> list[AuditEvent]:
    async with owner_engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
                )
            ).all()
        )


async def set_limits(client: AsyncClient, owner_engine: AsyncEngine, **limits: int) -> dict:
    olga = await user_id(owner_engine, EMPLOYEE)
    response = await client.patch(f"/api/employees/{olga}", json=limits)
    assert response.status_code == 200, response.text
    return response.json()


async def clients_with_cases(client: AsyncClient, count: int) -> list[dict]:
    made = []
    for n in range(count):
        contact = await new_contact(client, last_name=f"Клиент{n}")
        [case] = (await open_case(client, contact["id"])).json()
        made.append({"contact": contact, "case": case})
    return made


async def _all_waiting(conn: AsyncConnection, count: int) -> bool:
    """Do `count` connections to this database wait for a lock? Asked again and again for
    up to ten seconds; the activity view is read afresh each time."""
    for _ in range(200):
        await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiting = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        if waiting >= count:
            return True
        await asyncio.sleep(0.05)
    return False


async def at_once_on_a_busy_log(
    owner_engine: AsyncEngine, calls: list[Awaitable[Response]]
) -> list[Response]:
    """Send requests at once while the audit log takes no writes, as in a slow moment of
    the database: each request reads the log and waits to write to it, or waits for
    another request. The log opens once every request waits, so timing does not decide
    the outcome: if two requests can count the same events, they all do."""
    async with owner_engine.begin() as conn:
        await conn.execute(text("LOCK TABLE audit_events IN SHARE MODE"))
        sent = asyncio.gather(*calls)
        reached = await _all_waiting(conn, len(calls))
    answers = await sent
    assert reached, "the requests never reached the log"
    return answers


async def test_what_an_employee_opens_is_logged_once_a_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    [first] = await clients_with_cases(client, 1)
    await client.get(f"{CONTACTS}/{first['contact']['id']}")  # the specialist is not logged
    await give_cases(owner_engine, EMPLOYEE)

    await switch_to(client, EMPLOYEE)
    for _ in range(3):
        assert (await client.get(f"{CONTACTS}/{first['contact']['id']}")).status_code == 200
    assert (await client.get(f"/api/cases/{first['case']['id']}")).status_code == 200

    olga = str(await user_id(owner_engine, EMPLOYEE))
    [viewed] = await events(owner_engine, "contacts.viewed")
    assert (str(viewed.actor_id), viewed.target_id) == (olga, first["contact"]["id"])
    [case_viewed] = await events(owner_engine, "cases.viewed")
    assert case_viewed.target_id == first["case"]["id"]


async def test_past_the_card_limit_the_employee_waits_for_the_next_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    made = await clients_with_cases(client, 3)
    await give_cases(owner_engine, EMPLOYEE)
    assert (await set_limits(client, owner_engine, daily_card_limit=2))["daily_card_limit"] == 2

    await switch_to(client, EMPLOYEE)
    cards = [f"{CONTACTS}/{m['contact']['id']}" for m in made]
    assert (await client.get(cards[0])).status_code == 200
    assert (await client.get(cards[1])).status_code == 200
    for _ in range(2):
        paused = await client.get(cards[2])
        assert paused.status_code == 429
        assert paused.json()["detail"] == "card_limit_reached"
    # A case counts as a card too; what was opened today still opens.
    assert (await client.get(f"/api/cases/{made[2]['case']['id']}")).status_code == 429
    assert (await client.get(cards[0])).status_code == 200

    # The specialist sees it once, with the employee's name, and can dismiss it.
    await switch_to(client, "anna@example.com")
    items = (await client.get("/api/reminders")).json()
    [signal] = [i for i in items if i["kind"] == "card_limit"]
    assert items[0] == signal  # before any date of the clients
    assert signal["employee_name"] == EMPLOYEE and signal["limit"] == 2
    assert len(await events(owner_engine, "employees.limit_reached")) == 1
    assert (await client.post("/api/reminders/dismiss", json={"key": signal["key"]})).is_success
    kinds = {i["kind"] for i in (await client.get("/api/reminders")).json()}
    assert "card_limit" not in kinds

    # The next day the employee goes on.
    WALL_CLOCK.at += timedelta(days=1)
    await switch_to(client, EMPLOYEE)
    assert (await client.get(cards[2])).status_code == 200


async def test_past_the_password_limit_no_password_is_revealed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    [one, two] = await clients_with_cases(client, 2)
    vfs = await system(client)
    first = await entry(
        client, system_id=vfs["id"], contact_id=one["contact"]["id"], login="a@x.by", password="1"
    )
    second = await entry(
        client, system_id=vfs["id"], contact_id=two["contact"]["id"], login="b@x.by", password="2"
    )
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_password_limit=1)

    await switch_to(client, EMPLOYEE)
    assert (await reveal(client, first["id"])).json()["password"] == "1"
    assert (await reveal(client, first["id"])).json()["password"] == "1"
    paused = await reveal(client, second["id"])
    assert paused.status_code == 429 and paused.json() == {"detail": "password_limit_reached"}

    await switch_to(client, "anna@example.com")
    kinds = {i["kind"] for i in (await client.get("/api/reminders")).json()}
    assert "password_limit" in kinds
    # The specialist has no limits of their own.
    assert (await reveal(client, second["id"])).json()["password"] == "2"


async def test_the_specialist_sets_the_limits_within_bounds(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    [employee] = (await client.get("/api/employees")).json()
    assert (employee["daily_card_limit"], employee["daily_password_limit"]) == (50, 20)
    olga = await user_id(owner_engine, EMPLOYEE)
    for limits in ({"daily_card_limit": 0}, {"daily_password_limit": 1001}):
        response = await client.patch(f"/api/employees/{olga}", json=limits)
        assert response.status_code == 422, limits
    updated = await set_limits(client, owner_engine, daily_card_limit=120, daily_password_limit=5)
    assert (updated["daily_card_limit"], updated["daily_password_limit"]) == (120, 5)
    [changed] = await events(owner_engine, "employees.updated")
    assert sorted(changed.details["changed"]) == ["daily_card_limit", "daily_password_limit"]


async def test_an_employee_reads_only_their_own_events_in_the_log(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    tenant_id = await with_employee(client, make_user, owner_engine)
    [one] = await clients_with_cases(client, 1)  # the specialist's events
    await give_cases(owner_engine, EMPLOYEE)
    await switch_to(client, EMPLOYEE)
    await client.get(f"{CONTACTS}/{one['contact']['id']}")
    olga = await user_id(owner_engine, EMPLOYEE)

    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id, employee_id=olga)
        actors = set(await session.scalars(select(AuditEvent.actor_id).distinct()))
        assert actors == {olga}
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        assert await session.scalar(select(func.count(func.distinct(AuditEvent.actor_id)))) == 2
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM audit_events"))).scalar_one() > 1


async def test_reveals_sent_at_once_stop_at_the_password_limit(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    made = await clients_with_cases(client, 6)
    vfs = await system(client)
    entries = [
        await entry(
            client,
            system_id=vfs["id"],
            contact_id=m["contact"]["id"],
            login=f"u{n}@x.by",
            password=f"secret-{n}",
        )
        for n, m in enumerate(made)
    ]
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_password_limit=2)

    await switch_to(client, EMPLOYEE)
    answers = await at_once_on_a_busy_log(owner_engine, [reveal(client, e["id"]) for e in entries])
    assert sorted(a.status_code for a in answers) == [200] * 2 + [429] * 4
    paused = {a.json()["detail"] for a in answers if a.status_code == 429}
    assert paused == {"password_limit_reached"}
    assert len(await events(owner_engine, "vault.password_revealed")) == 2
    assert len(await events(owner_engine, "employees.limit_reached")) == 1  # told once


async def test_cards_opened_at_once_stop_at_the_card_limit(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    made = await clients_with_cases(client, 6)
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_card_limit=2)

    await switch_to(client, EMPLOYEE)
    cards = [client.get(f"{CONTACTS}/{m['contact']['id']}") for m in made]
    answers = await at_once_on_a_busy_log(owner_engine, cards)
    assert sorted(a.status_code for a in answers) == [200] * 2 + [429] * 4
    assert len(await events(owner_engine, "contacts.viewed")) == 2
    assert len(await events(owner_engine, "employees.limit_reached")) == 1


async def test_past_the_card_limit_an_edit_gives_no_card(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    [one, two, three] = await clients_with_cases(client, 3)
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_card_limit=2)

    await switch_to(client, EMPLOYEE)
    # An edit answers with the card, so it opens the card; a case is on its client's card.
    assert (await client.patch(f"{CONTACTS}/{one['contact']['id']}", json={})).status_code == 200
    assert (await client.patch(f"/api/cases/{two['case']['id']}", json={})).status_code == 200
    assert (await client.patch(f"/api/cases/{one['case']['id']}", json={})).status_code == 200
    # Grouping cases is the specialist's alone (test_employee_cases.py).
    for paused in (
        await client.patch(f"{CONTACTS}/{three['contact']['id']}", json={}),
        await client.patch(f"/api/cases/{three['case']['id']}", json={}),
    ):
        assert (paused.status_code, paused.json().get("detail")) == (429, "card_limit_reached")
    viewed = await events(owner_engine, "contacts.viewed")
    assert [v.target_id for v in viewed] == [one["contact"]["id"], two["contact"]["id"]]

    # The specialist has no limits, and her edits are not logged as opens.
    await switch_to(client, "anna@example.com")
    assert (await client.patch(f"{CONTACTS}/{three['contact']['id']}", json={})).status_code == 200
    group = {"case_id": one["case"]["id"]}
    grouped = await client.post(f"/api/cases/{three['case']['id']}/group", json=group)
    assert grouped.status_code == 200
    assert len(await events(owner_engine, "contacts.viewed")) == 2


async def test_past_the_card_limit_no_file_or_account_of_the_client_is_given(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await with_employee(client, make_user, owner_engine)
    await turn_on(owner_engine, tenant_id)
    made = await clients_with_cases(client, 3)
    template = (await upload_template(client)).json()
    vfs = await system(client)
    files = []
    for m in made[1:]:
        contact_id = m["contact"]["id"]
        card = f"{CONTACTS}/{contact_id}"
        given = await client.post(f"{card}/templates", json={"template_id": template["id"]})
        document = await upload_document(client, contact_id)
        await entry(client, system_id=vfs["id"], contact_id=contact_id, login=f"{contact_id}@x.by")
        files.append(
            [
                f"{card}/templates/{given.json()['id']}/file",
                f"{card}/documents/{document.json()['id']}",
                f"{card}/vault",
            ]
        )
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET modules = array_append(modules, 'document_templates') "
                "WHERE email = :e"
            ),
            {"e": EMPLOYEE},
        )
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_card_limit=1)

    await switch_to(client, EMPLOYEE)
    # A client's filled template, file or accounts open the client's card: the first one
    # counts, the rest of the same card are free.
    for url in files[0]:
        assert (await client.get(url)).status_code == 200, url
    assert (await client.get(f"{CONTACTS}/{made[0]['contact']['id']}")).status_code == 429
    for url in files[1]:
        paused = await client.get(url)
        assert (paused.status_code, paused.json().get("detail")) == (429, "card_limit_reached"), url
    viewed = await events(owner_engine, "contacts.viewed")
    assert [v.target_id for v in viewed] == [made[1]["contact"]["id"]]


async def card_sections(client: AsyncClient, contact_id: str, case_id: str) -> dict:
    """What the card shows of a client beyond its header: read by the specialist, who has
    no limit, to see that nothing changed."""
    urls = {
        "card": f"{CONTACTS}/{contact_id}",
        "trips": f"{CONTACTS}/{contact_id}/trips",
        "finance": f"/api/cases/{case_id}/finance",
        "package": f"/api/cases/{case_id}/package",
    }
    out = {}
    for name, url in urls.items():
        response = await client.get(url)
        assert response.status_code == 200, (url, response.text)
        out[name] = response.json()
    return out


async def test_past_the_card_limit_no_section_of_the_card_is_given_or_changed(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await with_employee(client, make_user, owner_engine)
    sections = ["stay_calculator", "package_check"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET modules = modules || CAST(:m AS varchar[]) WHERE id = :t"),
            {"m": sections, "t": tenant_id},
        )
        await conn.execute(
            text("UPDATE users SET modules = modules || CAST(:m AS varchar[]) WHERE email = :e"),
            {"m": sections, "e": EMPLOYEE},
        )
    [one, two] = await clients_with_cases(client, 2)
    contact, case = two["contact"]["id"], two["case"]["id"]
    poland = (await countries(client))["PL"]
    trip = await client.post(
        f"{CONTACTS}/{contact}/trips",
        json={"country_id": poland, "entered_on": "2026-01-10", "left_on": "2026-01-20"},
    )
    await client.patch(f"/api/cases/{case}/finance", json={"price": "1234.00"})
    money = {"amount": "100", "currency": "BYN"}
    payment = await client.post(
        f"/api/cases/{case}/payments", json={"paid_on": "2026-05-15", "official": True} | money
    )
    spent = {"spent_on": "2026-05-16", "description": "Перевод"}
    expense = await client.post(f"/api/cases/{case}/expenses", json=spent | money)
    item = await client.post(f"/api/checklists/{poland}/items", json={"name": "Билеты"})
    consultation = await client.post(
        f"{CONTACTS}/{contact}/consultations", json={"held_on": "2026-05-01", "notes": "Анна"}
    )
    await client.put(f"{CONTACTS}/{contact}/questionnaire", json={"note": "Анна"})
    trip_id, item_id = trip.json()["id"], item.json()["id"]
    payment_id = payment.json()["payments"][0]["id"]
    expense_id = expense.json()["expenses"][0]["id"]
    before = await card_sections(client, contact, case)
    assert before["finance"]["price"] == "1234.00"
    assert before["card"]["questionnaire"]["note"] == "Анна"
    await give_cases(owner_engine, EMPLOYEE)
    await set_limits(client, owner_engine, daily_card_limit=1)

    await switch_to(client, EMPLOYEE)
    assert (await client.get(f"{CONTACTS}/{one['contact']['id']}")).status_code == 200
    # Each section of the other card, read or edited, opens that card: past the limit
    # it gives nothing and changes nothing.
    routes = [
        ("GET", f"{CONTACTS}/{contact}/trips", None),
        ("POST", f"{CONTACTS}/{contact}/trips", {"country_id": poland, "entered_on": "2026-02-01"}),
        ("PATCH", f"/api/trips/{trip_id}", {"note": "Ольга"}),
        ("GET", f"/api/cases/{case}/finance", None),
        ("PATCH", f"/api/cases/{case}/finance", {"price": "1.00"}),
        ("POST", f"/api/cases/{case}/payments", {"paid_on": "2026-05-17"} | money),
        ("PATCH", f"/api/payments/{payment_id}", {"amount": "1"}),
        ("DELETE", f"/api/payments/{payment_id}", None),
        ("POST", f"/api/cases/{case}/expenses", spent | money),
        ("PATCH", f"/api/expenses/{expense_id}", {"amount": "1"}),
        ("DELETE", f"/api/expenses/{expense_id}", None),
        ("GET", f"/api/cases/{case}/package", None),
        ("PUT", f"/api/cases/{case}/package/{item_id}", {"state": "present"}),
        ("PATCH", f"/api/consultations/{consultation.json()['id']}", {"notes": "Ольга"}),
        ("PUT", f"{CONTACTS}/{contact}/questionnaire", {"note": "Ольга"}),
    ]
    answers = {}
    for method, url, body in routes:
        response = await client.request(method, url, json=body)
        answer = response.json()
        detail = answer.get("detail") if isinstance(answer, dict) else None
        answers[f"{method} {url}"] = (response.status_code, detail)
    assert answers == {route: (429, "card_limit_reached") for route in answers}
    viewed = await events(owner_engine, "contacts.viewed")
    assert [v.target_id for v in viewed] == [one["contact"]["id"]]

    await switch_to(client, "anna@example.com")
    assert await card_sections(client, contact, case) == before

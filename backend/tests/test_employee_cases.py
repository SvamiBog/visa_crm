"""An employee sees only their cases (вопрос 17, docs/decisions.md → «Сотрудники: только
свои кейсы»): the cases the specialist gave them, the rest of those cases' submission
groups, the clients of those cases, and the clients and cases they created themselves."""

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import AuditEvent, Contact, ContactNote, VisaCase
from tests.support.helpers import MakeUser, give_cases, login
from tests.test_cases import CONTACTS, as_specialist, card, new_contact, open_case

EMPLOYEE = "olga@example.com"
WORK_MODULES = ["clients", "cases", "vault", "finances", "tasks", "country_rules", "analytics"]


async def with_employee(client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine):
    """Anna's workspace with Olga, an employee with every core module; Anna logged in."""
    tenant_id = await as_specialist(client, make_user)
    await make_user(EMPLOYEE, "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = :m WHERE email = :e"),
            {"m": WORK_MODULES, "e": EMPLOYEE},
        )
    return tenant_id


async def user_id(owner_engine: AsyncEngine, email: str) -> uuid.UUID:
    async with owner_engine.connect() as conn:
        return (
            await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).scalar_one()


async def switch_to(client: AsyncClient, email: str) -> None:
    await client.post("/api/auth/logout")
    client.cookies.clear()
    await login(client, email)


async def found(client: AsyncClient, query: str = "") -> list[str]:
    response = await client.post(f"{CONTACTS}/search", json={"query": query})
    assert response.status_code == 200, response.text
    return [row["full_name"] for row in response.json()["items"]]


async def test_employee_sees_only_given_cases_and_their_clients(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    anna_client = await new_contact(client, last_name="Ковалёва", phone="+375291112233")
    other = await new_contact(client, last_name="Петров", first_name="Иван")
    [given] = (await open_case(client, anna_client["id"])).json()
    [not_given] = (await open_case(client, anna_client["id"], destination="LT")).json()
    [others_case] = (await open_case(client, other["id"])).json()
    await give_cases(owner_engine, EMPLOYEE, given["id"])

    await switch_to(client, EMPLOYEE)
    assert await found(client) == ["Ковалёва Анна"]
    assert await found(client, "Петров") == []
    assert await found(client, "Иван Петров") == []
    assert await found(client, "Анна Ковалёва") == ["Ковалёва Анна"]
    assert await found(client, "2233") == ["Ковалёва Анна"]
    assert [c["id"] for c in (await card(client, anna_client["id"]))["cases"]] == [given["id"]]
    for url in (
        f"{CONTACTS}/{other['id']}",
        f"/api/cases/{others_case['id']}",
        f"/api/cases/{not_given['id']}",
    ):
        response = await client.get(url)
        assert response.status_code == 404 and response.json()["detail"] == "not_found", url
    board = (await client.get("/api/cases/board")).json()
    assert [c["id"] for c in board["cases"]] == [given["id"]]

    # The specialist still sees everything.
    await switch_to(client, "anna@example.com")
    assert sorted(await found(client)) == ["Ковалёва Анна", "Петров Иван"]


async def test_the_whole_submission_group_of_a_given_case_is_visible(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    mother = await new_contact(client, last_name="Ковалёва")
    son = await new_contact(client, last_name="Ковалёв", first_name="Пётр")
    stranger = await new_contact(client, last_name="Петров", first_name="Иван")
    await open_case(client, stranger["id"])
    response = await open_case(
        client,
        mother["id"],
        applicants=[{"contact_id": mother["id"]}, {"contact_id": son["id"]}],
    )
    assert response.status_code == 201, response.text
    mothers_case = next(c for c in response.json() if c["contact_id"] == mother["id"])
    await give_cases(owner_engine, EMPLOYEE, mothers_case["id"])

    await switch_to(client, EMPLOYEE)
    assert sorted(await found(client)) == ["Ковалёв Пётр", "Ковалёва Анна"]
    [visible] = (await card(client, son["id"]))["cases"]
    assert [m["contact_id"] for m in visible["group"]] == [mother["id"]]


async def test_employee_keeps_the_clients_and_cases_they_create(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    hidden = await new_contact(client, last_name="Петров", first_name="Иван")
    await open_case(client, hidden["id"])
    await open_case(client, hidden["id"], destination="LT")

    await switch_to(client, EMPLOYEE)
    mine = await new_contact(client, last_name="Новикова", first_name="Мария")
    response = await open_case(client, mine["id"])
    assert response.status_code == 201, response.text
    # Numbers go on across the workspace, though the employee does not see cases 1 and 2.
    assert [c["number"] for c in response.json()] == [3]
    assert await found(client) == ["Новикова Мария"]
    assert len((await card(client, mine["id"]))["cases"]) == 1

    await switch_to(client, "anna@example.com")
    assert sorted(await found(client)) == ["Новикова Мария", "Петров Иван"]


async def test_a_hidden_clients_phone_is_taken_without_pointing_to_the_card(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    await new_contact(client, last_name="Петров", phone="+375291112233")

    await switch_to(client, EMPLOYEE)
    response = await client.post(
        CONTACTS, json={"last_name": "Петрова", "phone": "+375 29 111-22-33"}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "phone_taken"}
    mine = await new_contact(client, last_name="Новикова", phone="+375295556677")
    response = await client.patch(f"{CONTACTS}/{mine['id']}", json={"phone": "+375291112233"})
    assert response.status_code == 409 and response.json() == {"detail": "phone_taken"}


async def test_rows_of_hidden_clients_are_not_visible_in_the_database(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    """Row-Level Security, not only the API: a query that forgets the rule finds nothing."""
    tenant_id = await with_employee(client, make_user, owner_engine)
    seen = await new_contact(client, last_name="Ковалёва")
    hidden = await new_contact(client, last_name="Петров")
    for contact in (seen, hidden):
        note = await client.post(f"{CONTACTS}/{contact['id']}/notes", json={"body": "звонок"})
        assert note.status_code == 201, note.text
    [given] = (await open_case(client, seen["id"])).json()
    await open_case(client, hidden["id"])
    await give_cases(owner_engine, EMPLOYEE, given["id"])
    olga = await user_id(owner_engine, EMPLOYEE)

    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id, employee_id=olga)
        for model, expected in ((Contact, 1), (VisaCase, 1), (ContactNote, 1)):
            count = await session.scalar(select(func.count()).select_from(model))
            assert count == expected, model.__tablename__
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        assert await session.scalar(select(func.count()).select_from(Contact)) == 2


async def test_specialist_gives_and_takes_back_cases(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}/employees"
    olga = str(await user_id(owner_engine, EMPLOYEE))

    listed = (await client.get(url)).json()
    assert [(e["id"], e["granted"]) for e in listed] == [(olga, False)]
    given = await client.put(url, json={"employee_ids": [olga]})
    assert given.status_code == 200, given.text
    assert [e["granted"] for e in given.json()] == [True]
    employees = (await client.get("/api/employees")).json()
    assert [e["case_count"] for e in employees] == [1]

    await switch_to(client, EMPLOYEE)
    assert (await client.get(f"/api/cases/{case['id']}")).status_code == 200
    # Only the specialist decides who sees a case.
    assert (await client.get(url)).status_code == 403
    assert (await client.put(url, json={"employee_ids": []})).status_code == 403

    await switch_to(client, "anna@example.com")
    assert (await client.put(url, json={"employee_ids": []})).status_code == 200
    await switch_to(client, EMPLOYEE)
    assert (await client.get(f"/api/cases/{case['id']}")).status_code == 404

    async with owner_engine.connect() as conn:
        events = (
            await conn.execute(
                select(AuditEvent.action, AuditEvent.details)
                .where(AuditEvent.action == "cases.access_updated")
                .order_by(AuditEvent.id)
            )
        ).all()
    assert [e.details for e in events] == [
        {"granted": [olga], "revoked": []},
        {"granted": [], "revoked": [olga]},
    ]


async def test_only_employees_of_the_workspace_can_be_given_a_case(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    other_tenant = await make_user("boris@example.com")
    await make_user("ivan@example.com", "employee", tenant_id=other_tenant)
    await with_employee(client, make_user, owner_engine)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}/employees"
    for stranger in ("ivan@example.com", "anna@example.com"):
        response = await client.put(
            url, json={"employee_ids": [str(await user_id(owner_engine, stranger))]}
        )
        assert response.status_code == 422, stranger
        assert response.json()["detail"] == "unknown_employee"


async def test_registrar_balances_are_for_the_specialist_only(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The balance with a registrar covers every case, so an employee does not see it."""
    await with_employee(client, make_user, owner_engine)
    assert (await client.get("/api/registrars/ledgers")).status_code == 200
    await switch_to(client, EMPLOYEE)
    response = await client.get("/api/registrars/ledgers")
    assert response.status_code == 403 and response.json()["detail"] == "forbidden"


async def test_every_table_of_a_client_or_case_is_under_the_employee_scope(
    owner_engine: AsyncEngine,
):
    """A table with contact_id or case_id must have the policy employee_scope (migration
    0031): a new one without it would show an employee every client's rows."""
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT DISTINCT col.table_name, EXISTS ("
                "  SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public'"
                "  AND p.tablename = col.table_name AND p.policyname = 'employee_scope') "
                "FROM information_schema.columns col "
                "WHERE col.table_schema = 'public' AND col.column_name IN ('contact_id', 'case_id')"
            )
        )
        tables = dict(rows.all())
    assert {"visa_cases", "vault_entries", "bookings"} <= set(tables)
    assert [name for name, scoped in tables.items() if not scoped] == []


async def test_an_employee_cannot_give_themselves_a_case(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    """Even a query the API never sends: in an employee's session the grants are read-only."""
    tenant_id = await with_employee(client, make_user, owner_engine)
    [case] = (await open_case(client, (await new_contact(client))["id"])).json()
    olga = await user_id(owner_engine, EMPLOYEE)
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id, employee_id=olga)
        try:
            await session.execute(
                text(
                    "INSERT INTO employee_cases (user_id, case_id, tenant_id) VALUES (:u, :c, :t)"
                ),
                {"u": olga, "c": case["id"], "t": tenant_id},
            )
        except DBAPIError as error:
            assert "row-level security" in str(error.orig)
        else:
            raise AssertionError("an employee wrote a grant")


async def test_the_same_result_reaches_only_the_family_cases_the_employee_sees(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Pilot fix 7: like the fields a family shares, the result goes on only to the
    group's cases the employee sees. Given a case of a family, she sees and files the
    whole family; a case she made herself, which the specialist grouped with one of hers,
    does not reach the specialist's case."""
    await with_employee(client, make_user, owner_engine)
    visa = {"outcome": "visa", "decided_on": "2026-09-10", "result_to_group": True}
    mother = await new_contact(client, last_name="Ковалёва")
    son = await new_contact(client, last_name="Ковалёв", first_name="Пётр")
    family = await open_case(
        client,
        mother["id"],
        applicants=[{"contact_id": mother["id"]}, {"contact_id": son["id"]}],
    )
    assert family.status_code == 201, family.text
    mothers_case, sons_case = family.json()
    await give_cases(owner_engine, EMPLOYEE, mothers_case["id"])
    stranger = await new_contact(client, last_name="Петров", first_name="Иван")
    [strangers_case] = (await open_case(client, stranger["id"])).json()

    await switch_to(client, EMPLOYEE)
    own = await new_contact(client, last_name="Новикова", first_name="Мария")
    [own_case] = (await open_case(client, own["id"])).json()
    await switch_to(client, "anna@example.com")
    joined = await client.post(
        f"/api/cases/{strangers_case['id']}/group", json={"case_id": own_case["id"]}
    )
    assert joined.status_code == 200, joined.text

    await switch_to(client, EMPLOYEE)
    response = await client.patch(f"/api/cases/{mothers_case['id']}", json=visa)
    assert response.status_code == 200, response.text
    response = await client.patch(f"/api/cases/{own_case['id']}", json=visa)
    assert response.status_code == 200, response.text
    assert response.json()["group"] == []

    await switch_to(client, "anna@example.com")
    sons = (await client.get(f"/api/cases/{sons_case['id']}")).json()
    assert (sons["outcome"], sons["stage"]) == ("visa", "result")
    strangers = (await client.get(f"/api/cases/{strangers_case['id']}")).json()
    assert (strangers["outcome"], strangers["stage"]) == (None, "waiting")


# --- Submission groups (security audit run-1: M1, L1) --------------------------------
# Which cases an employee reaches follows the specialist's ticks and grouping: a given
# case opens its whole group (employee_sees_case), so only the specialist puts cases in
# a group or takes them out, like the ticks themselves.


async def group_events(owner_engine: AsyncEngine) -> list[tuple[str, str | None, str | None]]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            select(AuditEvent.action, AuditEvent.target_id, AuditEvent.actor_email)
            .where(AuditEvent.action.in_(["cases.grouped", "cases.ungrouped"]))
            .order_by(AuditEvent.id)
        )
        return [tuple(row) for row in rows.all()]


async def group_exists(owner_engine: AsyncEngine, group_id: str) -> bool:
    async with owner_engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM submission_groups WHERE id = :g)"),
                {"g": group_id},
            )
        ).scalar_one()


async def group_of(client: AsyncClient, case_id: str) -> str | None:
    response = await client.get(f"/api/cases/{case_id}")
    assert response.status_code == 200, response.text
    return response.json()["group_id"]


async def own_case_in_a_hidden_family(client: AsyncClient) -> dict[str, dict]:
    """Olga's own case X, which Anna put in a family's group with the cases P and Q that
    Olga was never given; Anna logged in at the end."""
    await switch_to(client, EMPLOYEE)
    mine = await new_contact(client, last_name="Новикова", first_name="Мария")
    [own] = (await open_case(client, mine["id"])).json()

    await switch_to(client, "anna@example.com")
    family = await new_contact(client, last_name="Ковалёва")
    response = await open_case(
        client, family["id"], applicants=[{"contact_id": family["id"]}, {"first_name": "Пётр"}]
    )
    assert response.status_code == 201, response.text
    p, q = response.json()
    joined = await client.post(f"/api/cases/{p['id']}/group", json={"case_id": own["id"]})
    assert joined.status_code == 200, joined.text
    return {"own": own, "p": p, "q": q, "family": family, "group": joined.json()["group_id"]}


async def test_an_employee_cannot_move_a_given_case_into_a_group_with_hidden_cases(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    made = await own_case_in_a_hidden_family(client)
    other = await new_contact(client, last_name="Петров", first_name="Иван")
    [given] = (await open_case(client, other["id"])).json()
    await give_cases(owner_engine, EMPLOYEE, given["id"])
    hidden = (
        f"/api/cases/{made['p']['id']}",
        f"/api/cases/{made['q']['id']}",
        f"{CONTACTS}/{made['family']['id']}",
    )

    await switch_to(client, EMPLOYEE)
    for url in hidden:
        assert (await client.get(url)).status_code == 404, url
    for case_id, body in (
        (made["own"]["id"], {"case_id": given["id"]}),
        (given["id"], {"case_id": made["own"]["id"]}),
    ):
        response = await client.post(f"/api/cases/{case_id}/group", json=body)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "forbidden"
    for url in hidden:
        assert (await client.get(url)).status_code == 404, url
    assert await group_of(client, given["id"]) is None
    assert [e for e in await group_events(owner_engine) if e[2] == EMPLOYEE] == []

    # The specialist's grouping still decides: a given case she groups opens the family.
    await switch_to(client, "anna@example.com")
    joined = await client.post(f"/api/cases/{given['id']}/group", json={"case_id": made["p"]["id"]})
    assert joined.status_code == 200, joined.text
    assert joined.json()["group_id"] == made["group"]
    await switch_to(client, EMPLOYEE)
    for url in hidden:
        assert (await client.get(url)).status_code == 200, url


async def test_an_employee_leaving_a_group_keeps_the_hidden_cases_in_it(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    made = await own_case_in_a_hidden_family(client)
    before = await group_events(owner_engine)

    await switch_to(client, EMPLOYEE)
    response = await client.delete(f"/api/cases/{made['own']['id']}/group")
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "forbidden"

    await switch_to(client, "anna@example.com")
    for case in (made["own"], made["p"], made["q"]):
        assert await group_of(client, case["id"]) == made["group"]
    assert await group_exists(owner_engine, made["group"])
    assert await group_events(owner_engine) == before


async def test_a_given_case_leaves_a_family_of_three_only_by_the_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await with_employee(client, make_user, owner_engine)
    family = await new_contact(client, last_name="Ковалёва")
    response = await open_case(
        client,
        family["id"],
        applicants=[{"contact_id": family["id"]}, {"first_name": "Пётр"}, {"first_name": "Ева"}],
    )
    assert response.status_code == 201, response.text
    r1, r2, r3 = response.json()
    group = r1["group_id"]
    await give_cases(owner_engine, EMPLOYEE, r1["id"])

    await switch_to(client, EMPLOYEE)
    for case in (r2, r3):
        assert await group_of(client, case["id"]) == group
    response = await client.delete(f"/api/cases/{r1['id']}/group")
    assert response.status_code == 403 and response.json()["detail"] == "forbidden"

    await switch_to(client, "anna@example.com")
    for case in (r1, r2, r3):
        assert await group_of(client, case["id"]) == group
    assert await group_events(owner_engine) == []

    # The specialist takes the given case out: the other two stay a family.
    assert (await client.delete(f"/api/cases/{r1['id']}/group")).status_code == 204
    assert await group_of(client, r1["id"]) is None
    for case in (r2, r3):
        assert await group_of(client, case["id"]) == group
    assert await group_exists(owner_engine, group)
    # A group of one is dissolved, and every case that leaves it is in the log.
    assert (await client.delete(f"/api/cases/{r2['id']}/group")).status_code == 204
    assert await group_of(client, r3["id"]) is None
    assert not await group_exists(owner_engine, group)
    assert await group_events(owner_engine) == [
        ("cases.ungrouped", case["id"], "anna@example.com") for case in (r1, r2, r3)
    ]

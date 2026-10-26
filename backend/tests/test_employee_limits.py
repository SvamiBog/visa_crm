"""Daily limits of an employee (backlog Т.14): what they open goes to the log, past the
limit they are paused until the next day, and the specialist sees it in «Требует
внимания». Rules: tests/test_employee_limit_rules.py."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import AuditEvent
from tests.helpers import WALL_CLOCK, MakeUser, give_cases
from tests.test_cases import CONTACTS, new_contact, open_case
from tests.test_employee_cases import EMPLOYEE, switch_to, user_id, with_employee
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

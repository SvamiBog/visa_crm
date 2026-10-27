"""The package check of a case through the API (backlog 2.3, part 1): the case's
checklist with what the specialist marked and each item's status at the submission
date; the module, the workspace and the audit log."""

import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import PackageItem
from tests.helpers import MakeUser, give_cases, login, workspace_today


def day(offset: int) -> str:
    return (workspace_today() + timedelta(days=offset)).isoformat()


async def turn_on(owner_engine: AsyncEngine, tenant_id: uuid.UUID | None) -> None:
    """Stage 2 modules are not in a new workspace by default: the admin turns them on."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_append(modules, 'package_check') WHERE id = :t"
            ),
            {"t": tenant_id},
        )


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, email="anna@example.com"
) -> uuid.UUID | None:
    client.cookies.clear()
    tenant_id = await make_user(email)
    await turn_on(owner_engine, tenant_id)
    await login(client, email)
    return tenant_id


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    client.cookies.clear()
    await make_user("root@example.com", "admin")
    await login(client, "root@example.com")


async def relogin(client: AsyncClient, email: str) -> None:
    client.cookies.clear()
    await login(client, email)


async def countries(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/reference/countries")
    if response.status_code == 403:
        response = await client.get("/api/admin/reference/countries")
    return {c["code"]: c["id"] for c in response.json()}


async def shared(client: AsyncClient, country: str, **fields) -> dict:
    response = await client.post(f"/api/admin/checklists/{country}/items", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def own(client: AsyncClient, country: str, **fields) -> dict:
    response = await client.post(f"/api/checklists/{country}/items", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, destination: str = "PL", **fields) -> dict:
    contact = (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    codes = await countries(client)
    response = await client.post(
        f"/api/contacts/{contact['id']}/cases",
        json={
            "destination_id": codes[destination],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": contact["id"]}],
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


async def package(client: AsyncClient, case: dict) -> dict:
    response = await client.get(f"/api/cases/{case['id']}/package")
    assert response.status_code == 200, response.text
    return response.json()


async def mark(client: AsyncClient, case: dict, item: dict, **body):
    return await client.put(f"/api/cases/{case['id']}/package/{item['id']}", json=body)


def by_name(result: dict) -> dict[str, dict]:
    return {i["name"]: i for i in result["items"]}


async def test_a_case_gets_the_items_of_its_country_submission_and_purpose(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    await shared(client, codes["PL"], name="Анкета")
    await shared(client, codes["PL"], name="Справка с работы", required=False)
    await shared(client, codes["PL"], name="Приглашение", purposes=["visit"])
    await shared(client, codes["PL"], name="Бронь отеля", purposes=["tourism"])
    await shared(client, codes["PL"], name="ВНЖ Турции", submission_id=codes["TR"])
    await shared(client, codes["DE"], name="Страховка для Германии")
    old = await shared(client, codes["PL"], name="Старая справка")
    switched = await client.patch(
        f"/api/admin/checklists/items/{old['id']}", json={"is_active": False}
    )
    assert switched.status_code == 200

    await as_specialist(client, make_user, owner_engine)
    guest = await client.post(
        "/api/dictionaries/purposes", json={"name": "Гостевая", "kind": "visit"}
    )
    await own(client, codes["PL"], name="Билеты")
    case = await open_case(client, purpose_id=guest.json()["id"])

    result = await package(client, case)
    assert [i["name"] for i in result["items"]] == [
        "Анкета",
        "Справка с работы",
        "Приглашение",
        "Билеты",
    ]
    assert [i["own"] for i in result["items"]] == [False, False, False, True]
    assert {i["status"] for i in result["items"]} == {"missing"}
    assert [i["state"] for i in result["items"]] == [None] * 4
    # No submission date yet: freshness is counted to today.
    assert result["on"] == day(0) and result["on_submission"] is False
    assert result["ready"] is False
    assert result["counts"] == {
        "ok": 0,
        "missing": 4,
        "no_date": 0,
        "expired": 0,
        "not_needed": 0,
    }


async def test_marks_give_statuses_at_the_submission_date(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    bank = await shared(client, codes["PL"], name="Выписка из банка", max_age_days=30)
    form = await shared(client, codes["PL"], name="Анкета")
    await shared(client, codes["PL"], name="Справка с работы", required=False)

    await as_specialist(client, make_user, owner_engine)
    insurance = await own(client, codes["PL"], name="Страховка", max_age_days=10)
    case = await open_case(client, submission_on=day(20))

    marked = await mark(client, case, bank, state="present", document_date=day(-5))
    assert marked.status_code == 200, marked.text
    assert marked.json()["status"] == "ok"  # 25 days old at submission
    assert (await mark(client, case, form, state="present")).json()["status"] == "ok"
    undated = await mark(client, case, insurance, state="present")
    assert undated.json()["status"] == "no_date"

    result = await package(client, case)
    assert result["on"] == day(20) and result["on_submission"] is True
    items = by_name(result)
    assert items["Выписка из банка"]["document_date"] == day(-5)
    assert items["Справка с работы"]["status"] == "missing"
    assert result["ready"] is False  # the insurance has no date

    await mark(client, case, insurance, state="not_needed")
    assert (await package(client, case))["ready"] is True  # the optional item does not hold

    moved = await client.patch(f"/api/cases/{case['id']}", json={"submission_on": day(30)})
    assert moved.status_code == 200, moved.text
    result = await package(client, case)
    assert by_name(result)["Выписка из банка"]["status"] == "expired"  # 35 days old
    assert result["ready"] is False
    assert result["counts"] == {
        "ok": 1,
        "missing": 1,
        "no_date": 0,
        "expired": 1,
        "not_needed": 1,
    }

    wrong = await mark(client, case, form, state="missing", document_date=day(-1))
    assert wrong.status_code == 422 and wrong.json()["detail"] == "document_date_needs_present"
    unknown = await mark(client, case, form, state="lost")
    assert unknown.status_code == 422


async def test_a_document_cannot_be_dated_after_today(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    bank = await own(client, codes["PL"], name="Выписка из банка", max_age_days=30)
    case = await open_case(client, submission_on=day(20))

    # A mistyped year would make an old certificate look fresh.
    future = await mark(client, case, bank, state="present", document_date=day(1))
    assert future.status_code == 422 and future.json()["detail"] == "document_date_in_future"
    assert (await package(client, case))["items"][0]["state"] is None
    today = await mark(client, case, bank, state="present", document_date=day(0))
    assert today.status_code == 200 and today.json()["status"] == "ok"


async def test_a_switched_off_item_leaves_the_package_but_keeps_its_mark(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    form = await shared(client, codes["PL"], name="Анкета")
    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    assert (await mark(client, case, form, state="present")).status_code == 200

    await relogin(client, "root@example.com")
    await client.patch(f"/api/admin/checklists/items/{form['id']}", json={"is_active": False})
    await relogin(client, "anna@example.com")
    assert (await package(client, case))["items"] == []
    assert (await mark(client, case, form, state="missing")).status_code == 404

    await relogin(client, "root@example.com")
    await client.patch(f"/api/admin/checklists/items/{form['id']}", json={"is_active": True})
    await relogin(client, "anna@example.com")
    assert by_name(await package(client, case))["Анкета"]["state"] == "present"

    # The specialist's own items go the same way.
    tickets = await own(client, codes["PL"], name="Билеты")
    assert (await mark(client, case, tickets, state="not_needed")).status_code == 200
    await client.patch(f"/api/checklists/items/{tickets['id']}", json={"is_active": False})
    assert "Билеты" not in by_name(await package(client, case))
    assert (await mark(client, case, tickets, state="present")).status_code == 404
    await client.patch(f"/api/checklists/items/{tickets['id']}", json={"is_active": True})
    assert by_name(await package(client, case))["Билеты"]["state"] == "not_needed"


async def test_items_outside_the_case_checklist_are_not_found(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    german = await shared(client, codes["DE"], name="Страховка для Германии")
    turkish = await shared(client, codes["PL"], name="ВНЖ Турции", submission_id=codes["TR"])
    tourist = await shared(client, codes["PL"], name="Бронь отеля", purposes=["tourism"])

    await as_specialist(client, make_user, owner_engine)
    case = await open_case(client)
    for item in (german, turkish, tourist, {"id": str(uuid.uuid4())}):
        response = await mark(client, case, item, state="present")
        assert response.status_code == 404 and response.json()["detail"] == "not_found"
    missing_case = {"id": str(uuid.uuid4())}
    assert (await client.get(f"/api/cases/{missing_case['id']}/package")).status_code == 404


async def test_the_package_stays_in_the_workspace_and_needs_its_module(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    anna = await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    tickets = await own(client, codes["PL"], name="Билеты")
    case = await open_case(client)
    assert (await mark(client, case, tickets, state="present")).status_code == 200

    boris = await as_specialist(client, make_user, owner_engine, "boris@example.com")
    assert (await client.get(f"/api/cases/{case['id']}/package")).status_code == 404
    assert (await mark(client, case, tickets, state="missing")).status_code == 404
    his_case = await open_case(client)
    # Anna's own item is not in Boris's checklist, even for a case of the same country.
    assert (await mark(client, his_case, tickets, state="present")).status_code == 404
    async with sessionmaker() as session:
        scope_to_tenant(session, boris)
        assert (await session.scalars(select(PackageItem))).all() == []
    async with sessionmaker() as session:
        scope_to_tenant(session, anna)
        assert len((await session.scalars(select(PackageItem))).all()) == 1

    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=anna)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    denied = await client.get(f"/api/cases/{case['id']}/package")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

    client.cookies.clear()
    await make_user("vera@example.com")  # her workspace has no «package_check»
    await login(client, "vera@example.com")
    her_case = await open_case(client)
    denied = await client.get(f"/api/cases/{her_case['id']}/package")
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"


async def test_marking_is_in_the_audit_log_with_ids_only(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    codes = await countries(client)
    bank = await own(client, codes["PL"], name="Выписка из банка", max_age_days=30)
    case = await open_case(client)
    await mark(client, case, bank, state="present", document_date=day(-3))

    event = (await client.get("/api/audit")).json()["events"][0]
    assert event["action"] == "cases.package_item_marked"
    # Ids and the state only: no document name or date.
    assert event["details"] == {
        "contact": case["contact_id"],
        "item": bank["id"],
        "state": "present",
    }


# --- «Требует внимания» and the home screen (part 2) ---------------------------------


async def package_reminders(client: AsyncClient, **params) -> list[dict]:
    response = await client.get("/api/reminders", params=params)
    assert response.status_code == 200, response.text
    return [i for i in response.json() if i["kind"] == "package"]


async def run(owner_engine: AsyncEngine, sql: str, **params) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(text(sql), params)


async def test_an_incomplete_package_is_in_attention_before_the_submission(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    form = await shared(client, codes["PL"], name="Анкета")
    bank = await shared(client, codes["PL"], name="Выписка из банка", max_age_days=30)
    await shared(client, codes["PL"], name="Справка с работы", required=False)

    anna = await as_specialist(client, make_user, owner_engine)
    soon = await open_case(client, submission_on=day(5))
    later = await open_case(client, submission_on=day(8))  # not within the week yet
    await open_case(client, "DE", submission_on=day(2))  # no checklist: nothing to collect
    await open_case(client)  # no submission date

    [item] = await package_reminders(client)
    assert (item["case_id"], item["due_on"], item["days_left"]) == (soon["id"], day(5), 5)
    # The required documents still to collect; the optional one does not count.
    assert item["missing"] == 2 == (await package(client, soon))["missing"]
    assert item["key"] == f"package:{soon['id']}:{day(-2)}"
    assert len(await package_reminders(client, contact_id=soon["contact_id"])) == 1
    assert await package_reminders(client, contact_id=later["contact_id"]) == []

    await mark(client, soon, form, state="present")
    assert [i["missing"] for i in await package_reminders(client)] == [1]

    # «Готово» hides this occurrence; the day before the submission brings it back.
    done = await client.post("/api/reminders/dismiss", json={"key": item["key"]})
    assert done.status_code == 204
    assert await package_reminders(client) == []
    moved = await client.patch(f"/api/cases/{soon['id']}", json={"submission_on": day(1)})
    assert moved.status_code == 200, moved.text
    [item] = await package_reminders(client)
    assert item["key"] == f"package:{soon['id']}:{day(0)}"

    # The window is the specialist's: ten days bring the later case in.
    settings = (await client.get("/api/reminders/settings")).json()
    assert settings["package_days"] == 7
    saved = await client.put("/api/reminders/settings", json=settings | {"package_days": 10})
    assert saved.status_code == 200 and saved.json()["package_days"] == 10
    assert {i["case_id"] for i in await package_reminders(client)} == {soon["id"], later["id"]}

    # A collected package leaves the list.
    await mark(client, soon, bank, state="present", document_date=day(-1))
    assert [i["case_id"] for i in await package_reminders(client)] == [later["id"]]

    # An employee sees only the cases given to them, and only with the module.
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=anna)
    await run(
        owner_engine,
        "UPDATE users SET modules = '{clients,cases,tasks,package_check}' "
        "WHERE email = 'olga@example.com'",
    )
    await give_cases(owner_engine, "olga@example.com", soon["id"])
    await login(client, "olga@example.com")
    assert await package_reminders(client) == []
    await give_cases(owner_engine, "olga@example.com", later["id"])
    assert [i["case_id"] for i in await package_reminders(client)] == [later["id"]]
    await run(
        owner_engine,
        "UPDATE users SET modules = '{clients,cases,tasks}' WHERE email = 'olga@example.com'",
    )
    assert await package_reminders(client) == []

    # Without the module in the workspace there is no package to remind of.
    await relogin(client, "anna@example.com")
    await run(
        owner_engine,
        "UPDATE tenants SET modules = array_remove(modules, 'package_check') WHERE id = :t",
        t=anna,
    )
    assert await package_reminders(client) == []


async def test_upcoming_submissions_show_package_readiness(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_admin(client, make_user)
    codes = await countries(client)
    form = await shared(client, codes["PL"], name="Анкета")
    bank = await shared(client, codes["PL"], name="Выписка из банка", max_age_days=30)
    await shared(client, codes["PL"], name="Справка с работы", required=False)

    anna = await as_specialist(client, make_user, owner_engine)
    # A family submitting together: the mother has her documents, the daughter none.
    mother = (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    response = await client.post(
        f"/api/contacts/{mother['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": mother["id"]}, {"first_name": "Мария"}],
        },
    )
    assert response.status_code == 201, response.text
    family = response.json()
    for case in family:
        moved = await client.patch(f"/api/cases/{case['id']}", json={"submission_on": day(2)})
        assert moved.status_code == 200, moved.text
    await mark(client, family[0], form, state="present")
    await mark(client, family[0], bank, state="present", document_date=day(-3))
    ready = await open_case(client, submission_on=day(3))
    await mark(client, ready, form, state="present")
    await mark(client, ready, bank, state="present", document_date=day(-3))
    await open_case(client, "DE", submission_on=day(4))  # no checklist for Germany

    async def lines() -> list[dict | None]:
        home = await client.get("/api/home")
        assert home.status_code == 200, home.text
        return [line["package"] for line in home.json()["submissions"]["upcoming"]]

    assert await lines() == [
        {"ready": False, "missing": 2},  # the daughter's two required documents
        {"ready": True, "missing": 0},
        None,  # the stage is shown instead
    ]

    # An employee with the module sees the readiness of the cases given to them.
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=anna)
    await run(
        owner_engine,
        "UPDATE users SET modules = '{clients,cases,package_check}' "
        "WHERE email = 'olga@example.com'",
    )
    await give_cases(owner_engine, "olga@example.com", ready["id"])
    await login(client, "olga@example.com")
    assert await lines() == [{"ready": True, "missing": 0}]
    await run(
        owner_engine,
        "UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'",
    )
    assert await lines() == [None]

    await relogin(client, "anna@example.com")
    await run(
        owner_engine,
        "UPDATE tenants SET modules = array_remove(modules, 'package_check') WHERE id = :t",
        t=anna,
    )
    assert await lines() == [None, None, None]

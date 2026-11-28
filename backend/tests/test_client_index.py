"""Client index (backlog 1.5): tabs, filters, row summaries, the board and moving cases."""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, true
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api import cases as cases_api
from app.api.client_status import (
    Sort,
    Tab,
    filter_contacts,
    latest_consultation,
    sort_contacts,
    tab_of,
)
from app.core.db import scope_to_tenant
from app.models import CaseStage, Contact
from tests.support.helpers import MakeUser, give_cases, login, workspace_today

CONTACTS = "/api/contacts"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    return {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}


async def contact(client: AsyncClient, name: str, **fields) -> str:
    response = await client.post(CONTACTS, json={"first_name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def consult(client: AsyncClient, contact_id: str, **fields) -> dict:
    body = {"held_on": workspace_today().isoformat(), **fields}
    response = await client.post(f"{CONTACTS}/{contact_id}/consultations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, contact_id: str, destination="PL", **fields) -> dict:
    codes = await countries(client)
    response = await client.post(
        f"{CONTACTS}/{contact_id}/cases",
        json={
            "destination_id": codes[destination],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": contact_id}],
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


async def search(client: AsyncClient, **body) -> dict:
    response = await client.post(f"{CONTACTS}/search", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def names(result: dict) -> set[str]:
    return {row["full_name"] for row in result["items"]}


async def test_every_contact_is_in_one_tab(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    await contact(client, "Новый")
    await consult(client, await contact(client, "Думает"), decision="thinking")
    await consult(client, await contact(client, "Без решения"))
    await consult(client, await contact(client, "Да без кейса"), decision="yes")
    await consult(client, await contact(client, "Нет"), decision="no")
    await open_case(client, await contact(client, "В работе"))
    done = await open_case(client, await contact(client, "Получил визу"))
    await client.patch(f"/api/cases/{done['id']}", json={"outcome": "visa"})
    # A «thinking» client who comes back with a new case is in work.
    returning = await contact(client, "Вернулся")
    await consult(client, returning, decision="thinking")
    await open_case(client, returning)

    result = await search(client)
    assert result["counts"] == {
        "all": 8,
        "work": 2,
        "consult": 3,
        "thinking": 1,
        "archive": 2,
    }
    assert names(await search(client, tab="work")) == {"В работе", "Вернулся"}
    assert names(await search(client, tab="consult")) == {"Новый", "Без решения", "Да без кейса"}
    assert names(await search(client, tab="thinking")) == {"Думает"}
    assert names(await search(client, tab="archive")) == {"Нет", "Получил визу"}
    assert {row["tab"] for row in result["items"]} == {"work", "consult", "thinking", "archive"}


async def test_filters_narrow_rows_and_counts(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    channel = (await client.post("/api/dictionaries/channels", json={"name": "Таргет"})).json()
    registrar = (
        await client.post(
            "/api/dictionaries/registrars", json={"name": "Иван", "kind": "individual"}
        )
    ).json()
    poland = await contact(client, "Польша", lead_channel_id=channel["id"])
    await open_case(client, poland, registrar_id=registrar["id"])
    france = await contact(client, "Франция")
    french_case = await open_case(client, france, destination="FR")
    await client.patch(f"/api/cases/{french_case['id']}", json={"submission_on": "2026-12-01"})
    # A consultation about Poland counts for the country filter too.
    await consult(client, await contact(client, "Думает о Польше"), destination_ids=[codes["PL"]])

    by_country = await search(client, country_id=codes["PL"])
    assert names(by_country) == {"Польша", "Думает о Польше"}
    assert by_country["counts"]["all"] == 2
    assert names(await search(client, stage="documents")) == {"Франция"}
    assert names(await search(client, channel_id=channel["id"])) == {"Польша"}
    assert names(await search(client, registrar_id=registrar["id"])) == {"Польша"}
    assert names(await search(client, query="фран", tab="work")) == {"Франция"}
    # Words in any order narrow within the filters and the tabs, and the counts follow.
    in_words = await search(client, query="польше думает", country_id=codes["PL"])
    assert names(in_words) == {"Думает о Польше"}
    assert in_words["counts"] == {"all": 1, "work": 0, "consult": 1, "thinking": 0, "archive": 0}
    assert names(await search(client, query="польше думает", tab="work")) == set()


async def test_row_shows_the_case_in_work(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    anna = await contact(client, "Анна")
    consultation = await consult(client, anna, decision="thinking")
    old = await open_case(client, anna, destination="IT")
    await client.patch(f"/api/cases/{old['id']}", json={"outcome": "visa"})
    await client.post(
        f"{CONTACTS}/{anna}/cases",
        json={
            "consultation_id": consultation["id"],
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [{"contact_id": anna}, {"first_name": "Сергей"}, {"first_name": "Мария"}],
        },
    )
    [row] = [r for r in (await search(client, query="Анна"))["items"]]
    assert row["tab"] == "work" and row["cases_in_work"] == 1
    # The case in work is shown, not the newer-numbered finished one nor the older one.
    assert row["case"]["destination_id"] == codes["PL"]
    assert row["case"]["group_size"] == 3
    assert row["consultation"]["decision"] == "yes"


async def test_rows_without_the_cases_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    await open_case(client, await contact(client, "Анна"))
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    [row] = (await search(client))["items"]
    assert row["case"] is None
    # Without cases the date to sort by is the consultation's: none here.
    assert [r["id"] for r in (await search(client, sort="next"))["items"]] == [row["id"]]
    assert (await client.get("/api/cases/board")).status_code == 403


# A made-up number the visa center knows Anna's case by.
VAC_NUMBER = "V02QJX-7731"


async def given_to_olga(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, modules: list[str]
) -> dict[str, str]:
    """Anna said «yes» and has a case to Poland (the VAC number, registrar Ivan, at the
    submission stage); Boris has a case to France and no consultation. Both cases are
    given to the employee Olga, who has `modules` and is then signed in."""
    tenant_id = await as_specialist(client, make_user)
    ivan, petr = [
        (
            await client.post(
                "/api/dictionaries/registrars", json={"name": name, "kind": "individual"}
            )
        ).json()["id"]
        for name in ("Иван", "Пётр")
    ]
    anna = await contact(client, "Анна")
    await consult(client, anna, decision="yes")
    case = await open_case(client, anna, registrar_id=ivan)
    changed = await client.patch(
        f"/api/cases/{case['id']}", json={"vac_number": VAC_NUMBER, "stage": "submission"}
    )
    assert changed.json()["stage"] == "submission", changed.text
    await open_case(client, await contact(client, "Борис"), destination="FR")
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = :modules WHERE email = 'olga@example.com'"),
            {"modules": modules},
        )
    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    return {"case": case["id"], "ivan": ivan, "petr": petr}


async def test_case_fields_neither_find_nor_narrow_without_the_cases_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Search must not answer questions about a case the user may not open (audit L3):
    the VAC number, stage, registrar and destination of Anna's case give the same rows
    and counts as values that are nobody's. The country filter keeps the consultations."""
    ids = await given_to_olga(client, make_user, owner_engine, ["clients"])
    codes = await countries(client)
    assert (await client.get(f"/api/cases/{ids['case']}")).status_code == 403

    nobodys = await search(client, query="V02QJX-78")
    for probe in (VAC_NUMBER, "V02QJX-77", "QJX-773"):
        assert await search(client, query=probe) == nobodys
    assert nobodys["items"] == [] and nobodys["counts"]["all"] == 0
    everyone = await search(client)
    for tab in ("all", "work", "consult"):
        assert await search(client, tab=tab, stage="submission") == await search(
            client, tab=tab, stage="waiting"
        )
        assert await search(client, tab=tab, registrar_id=ids["ivan"]) == await search(
            client, tab=tab, registrar_id=ids["petr"]
        )
        assert await search(client, tab=tab, country_id=codes["PL"]) == await search(
            client, tab=tab, country_id=codes["IT"]
        )
    # Without the module a filter on the case does not narrow the list at all.
    assert await search(client, stage="submission") == everyone
    assert await search(client, registrar_id=ids["ivan"]) == everyone


async def test_tabs_go_by_consultations_alone_without_the_cases_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Without the module a client's tab is counted as if they had no cases (owner's
    decision 2026-11-22): the tab does not tell whether a case is in work or done."""
    ids = await given_to_olga(client, make_user, owner_engine, ["clients"])
    before = await search(client)
    assert names(before) == {"Анна", "Борис"}
    # Anna said «yes» and Boris has no consultation: both are «consultations».
    assert before["counts"] == {"all": 2, "work": 0, "consult": 2, "thinking": 0, "archive": 0}
    assert {row["tab"] for row in before["items"]} == {"consult"}

    # The specialist enters the result of Anna's case: nothing in Olga's search changes.
    client.cookies.clear()
    await login(client, "anna@example.com")
    done = await client.patch(f"/api/cases/{ids['case']}", json={"outcome": "visa"})
    assert done.json()["stage"] == "result", done.text
    client.cookies.clear()
    await login(client, "olga@example.com")
    assert await search(client) == before


async def test_case_fields_find_and_narrow_with_the_cases_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """With the module the same employee finds clients by the VAC number and the filters
    and tabs go by the cases, as for the specialist."""
    ids = await given_to_olga(client, make_user, owner_engine, ["clients", "cases"])
    codes = await countries(client)
    for probe in (VAC_NUMBER, "V02QJX-77", "QJX-773"):
        assert names(await search(client, query=probe)) == {"Анна"}
    assert await search(client, query="V02QJX-78") == await search(client, query="nobody")
    assert names(await search(client, stage="submission")) == {"Анна"}
    assert names(await search(client, stage="waiting")) == {"Борис"}
    assert names(await search(client, registrar_id=ids["ivan"])) == {"Анна"}
    assert names(await search(client, registrar_id=ids["petr"])) == set()
    assert names(await search(client, country_id=codes["PL"])) == {"Анна"}
    assert (await search(client))["counts"] == {
        "all": 2,
        "work": 2,
        "consult": 0,
        "thinking": 0,
        "archive": 0,
    }


async def test_board(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    registrar = (
        await client.post(
            "/api/dictionaries/registrars", json={"name": "Иван", "kind": "individual"}
        )
    ).json()
    waiting = await open_case(client, await contact(client, "Ждёт"), registrar_id=registrar["id"])
    recent = await open_case(client, await contact(client, "Свежая виза"), destination="FR")
    await client.patch(
        f"/api/cases/{recent['id']}",
        json={"outcome": "visa", "decided_on": workspace_today().isoformat()},
    )
    old = await open_case(client, await contact(client, "Давняя виза"))
    long_ago = (workspace_today() - timedelta(days=60)).isoformat()
    await client.patch(f"/api/cases/{old['id']}", json={"outcome": "visa", "decided_on": long_ago})
    await consult(
        client,
        await contact(client, "Думает"),
        decision="thinking",
        destination_ids=[codes["IT"]],
    )
    await consult(client, await contact(client, "Отказался"), decision="no")

    board = (await client.get("/api/cases/board")).json()
    assert {c["id"]: c["stage"] for c in board["cases"]} == {
        waiting["id"]: "waiting",
        recent["id"]: "result",
    }
    assert [(lead["full_name"], lead["tab"]) for lead in board["leads"]] == [("Думает", "thinking")]

    by_registrar = (
        await client.get("/api/cases/board", params={"registrar_id": registrar["id"]})
    ).json()
    assert [c["id"] for c in by_registrar["cases"]] == [waiting["id"]]
    assert by_registrar["leads"] == []
    by_country = (await client.get("/api/cases/board", params={"country_id": codes["IT"]})).json()
    assert by_country["cases"] == [] and len(by_country["leads"]) == 1


async def test_board_shows_results_of_the_chosen_period(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)

    def ago(days: int) -> str:
        return (workspace_today() - timedelta(days=days)).isoformat()

    shown_as = {(await open_case(client, await contact(client, "В работе")))["id"]: "В работе"}
    for name, days in (("Неделя", 7), ("Квартал", 80), ("Полгода", 170), ("Год", 300)):
        case = await open_case(client, await contact(client, name))
        await client.patch(
            f"/api/cases/{case['id']}", json={"outcome": "visa", "decided_on": ago(days)}
        )
        shown_as[case["id"]] = name
    case = await open_case(client, await contact(client, "Давно"))
    await client.patch(f"/api/cases/{case['id']}", json={"outcome": "visa", "decided_on": ago(500)})
    shown_as[case["id"]] = "Давно"

    async def shown(**params: str) -> set[str]:
        response = await client.get("/api/cases/board", params=params)
        assert response.status_code == 200, response.text
        return {shown_as[c["id"]] for c in response.json()["cases"]} - {"В работе"}

    assert await shown() == {"Неделя"}
    assert await shown(result_period="month") == {"Неделя"}
    assert await shown(result_period="half_year") == {"Неделя", "Квартал", "Полгода"}
    assert await shown(result_period="year") == {"Неделя", "Квартал", "Полгода", "Год"}
    assert await shown(result_period="all") == {"Неделя", "Квартал", "Полгода", "Год", "Давно"}
    # Own dates: both ends count, either may be left open.
    period = {"result_period": "dates"}
    assert await shown(**period, result_from=ago(300), result_to=ago(80)) == {
        "Квартал",
        "Полгода",
        "Год",
    }
    assert await shown(**period, result_from=ago(170)) == {"Неделя", "Квартал", "Полгода"}
    assert await shown(**period, result_to=ago(300)) == {"Год", "Давно"}

    # A case in work stays on the board whatever the period.
    board = (await client.get("/api/cases/board", params={**period, "result_to": ago(600)})).json()
    assert [shown_as[c["id"]] for c in board["cases"]] == ["В работе"]

    reversed_dates = {**period, "result_from": ago(80), "result_to": ago(300)}
    response = await client.get("/api/cases/board", params=reversed_dates)
    assert response.status_code == 422 and response.json()["detail"] == "dates_order"
    for wrong in ({"result_period": "week"}, {**period, "result_from": "0026-11-05"}):
        assert (await client.get("/api/cases/board", params=wrong)).status_code == 422


async def test_results_never_crowd_cases_in_work_off_the_board(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """The board holds a limited number of cases: with all results shown, the oldest case
    in work (the lowest number) still comes before any result."""
    await as_specialist(client, make_user)
    in_work = await open_case(client, await contact(client, "Ждёт"))
    for name in ("Первый", "Второй"):
        case = await open_case(client, await contact(client, name))
        await client.patch(
            f"/api/cases/{case['id']}",
            json={"outcome": "visa", "decided_on": workspace_today().isoformat()},
        )
    monkeypatch.setattr(cases_api, "BOARD_LIMIT", 2)

    board = (await client.get("/api/cases/board", params={"result_period": "all"})).json()
    assert len(board["cases"]) == 2
    assert in_work["id"] in {c["id"] for c in board["cases"]}


async def test_moving_a_group_on_the_board(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    anna = await contact(client, "Анна")
    family = (
        await client.post(
            f"{CONTACTS}/{anna}/cases",
            json={
                "destination_id": codes["PL"],
                "submission_country_id": codes["BY"],
                "applicants": [{"contact_id": anna}, {"first_name": "Сергей"}],
            },
        )
    ).json()
    ids = [c["id"] for c in family]
    moved = await client.post("/api/cases/stage", json={"case_ids": ids, "stage": "submission"})
    assert moved.status_code == 204
    board = (await client.get("/api/cases/board")).json()
    assert {c["stage"] for c in board["cases"]} == {"submission"}
    events = (await client.get("/api/audit")).json()["events"]
    assert [e["action"] for e in events[:2]] == ["cases.updated", "cases.updated"]
    assert (
        await client.post("/api/cases/stage", json={"case_ids": [], "stage": "review"})
    ).status_code == 422
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    foreign = await client.post("/api/cases/stage", json={"case_ids": ids, "stage": "review"})
    assert foreign.status_code == 404
    assert (await client.get("/api/cases/board")).json() == {"cases": [], "leads": []}


def order(result: dict) -> list[str]:
    return [row["full_name"] for row in result["items"]]


async def test_sorted_by_name_in_the_russian_alphabet(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    for name in ("Жуков", "Ёлкина", "Абрамов", "Яковлев", "Ежова"):
        await contact(client, name)

    # «Ё» goes with «Е», not before «А» as its code would put it.
    assert order(await search(client, sort="name")) == [
        "Абрамов",
        "Ежова",
        "Ёлкина",
        "Жуков",
        "Яковлев",
    ]
    assert order(await search(client, sort="-name"))[0] == "Яковлев"
    # By default the newest first, as before.
    assert order(await search(client))[0] == "Ежова"


async def test_sorted_by_the_next_date_with_blanks_last(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    today = workspace_today()
    soon, later = today + timedelta(days=10), today + timedelta(days=20)
    await consult(
        client,
        await contact(client, "Консультация"),
        held_on=(today - timedelta(days=30)).isoformat(),
    )
    late = await open_case(client, await contact(client, "Подача позже"))
    await client.patch(f"/api/cases/{late['id']}", json={"submission_on": later.isoformat()})
    early = await open_case(client, await contact(client, "Подача скоро"))
    await client.patch(f"/api/cases/{early['id']}", json={"submission_on": soon.isoformat()})
    # A returning client: the case in work counts, not the finished one.
    returning = await contact(client, "Вернулся")
    old = await open_case(client, returning, destination="IT")
    await client.patch(
        f"/api/cases/{old['id']}",
        json={"submission_on": (today - timedelta(days=90)).isoformat(), "outcome": "visa"},
    )
    again = await open_case(client, returning)
    await client.patch(
        f"/api/cases/{again['id']}", json={"submission_on": (soon + timedelta(days=5)).isoformat()}
    )
    # No next date: a result, a case without a submission date, a new contact.
    done = await open_case(client, await contact(client, "Результат"))
    await client.patch(
        f"/api/cases/{done['id']}",
        json={"submission_on": (today - timedelta(days=5)).isoformat(), "outcome": "visa"},
    )
    await open_case(client, await contact(client, "Без даты"))
    await contact(client, "Новый")

    blanks = {"Результат", "Без даты", "Новый"}
    ascending = order(await search(client, sort="next"))
    assert ascending[:4] == ["Консультация", "Подача скоро", "Вернулся", "Подача позже"]
    assert set(ascending[4:]) == blanks
    descending = order(await search(client, sort="-next"))
    assert descending[:4] == ["Подача позже", "Вернулся", "Подача скоро", "Консультация"]
    assert set(descending[4:]) == blanks


async def test_sorted_pages_neither_skip_nor_repeat(
    client: AsyncClient, make_user: MakeUser, monkeypatch
):
    from app.api import contacts as contacts_api

    monkeypatch.setattr(contacts_api, "SEARCH_LIMIT", 2)
    await as_specialist(client, make_user)
    for name in ("Иван", "Иван", "Иван", "Анна", "Борис"):
        await contact(client, name)

    seen: list[str] = []
    offset, more = 0, True
    while more:
        page = await search(client, sort="name", offset=offset)
        seen += [row["id"] for row in page["items"]]
        offset, more = offset + 2, page["has_more"]
    assert len(seen) == len(set(seen)) == 5


async def test_unknown_sort_is_rejected(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    response = await client.post(f"{CONTACTS}/search", json={"sort": "phone"})
    assert response.status_code == 422


async def test_the_index_finds_a_clients_cases_by_index(
    client: AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    """The tab, the shown case and the filters by a case look a client's cases up by the
    indexes on contact_id and consultation_id. With one condition «own case OR a case of
    their consultation» inside one EXISTS the database had no such path and read every
    case of every workspace once per client (audit 2026-11-27, H-1): 3.7 s for the list on
    ten workspaces of 1500 clients. Sequential scans are switched off here, so a scan that
    stays in the plan is one the planner had no other way for."""
    tenant_id = await as_specialist(client, make_user)
    codes = await countries(client)
    latest = latest_consultation()
    tab = tab_of(latest)
    base = select(Contact.id, tab.label("tab")).outerjoin(latest, true())
    queries = {
        "tabs": base,
        "one tab": base.where(tab == Tab.WORK.value),
        "filters": filter_contacts(
            base,
            latest,
            country_id=uuid.UUID(codes["PL"]),
            stage=CaseStage.DOCUMENTS,
            registrar_id=uuid.uuid4(),
            with_cases=True,
        ),
        "sorted by the next date": sort_contacts(base, latest, Sort.NEXT, with_cases=True),
    }
    async with sessionmaker() as session:
        scope_to_tenant(session, tenant_id)
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        for name, query in queries.items():
            sql = query.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
            plan = "\n".join((await session.scalars(text(f"EXPLAIN (COSTS OFF) {sql}"))).all())
            assert "Seq Scan on visa_cases" not in plan, f"{name}:\n{plan}"

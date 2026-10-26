"""Client index (backlog 1.5): tabs, filters, row summaries, the board and moving cases."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, give_cases, login, workspace_today

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
    assert (await client.get("/api/cases/board")).status_code == 403


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

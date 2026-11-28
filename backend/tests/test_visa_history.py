"""The client's visa history on the card (docs/clients-module.md → «Анкета консультации»,
backlog A3): the visas of the client's cases are in it by themselves."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support.helpers import MakeUser, give_cases, login, workspace_today
from tests.test_cases import as_specialist, card, family_of_three, new_contact, open_case


async def tourism(client: AsyncClient) -> str:
    response = await client.post(
        "/api/dictionaries/purposes", json={"name": "Туризм", "kind": "tourism"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def grant_visa(client: AsyncClient, case_id: str, **result) -> None:
    """The result «visa», valid for a year from a month ago unless the test says otherwise."""
    today = workspace_today()
    body = {
        "outcome": "visa",
        "visa_entries": "multiple",
        "visa_valid_from": str(today - timedelta(days=30)),
        "visa_valid_to": str(today + timedelta(days=335)),
        **result,
    }
    response = await client.patch(f"/api/cases/{case_id}", json=body)
    assert response.status_code == 200, response.text


async def history(client: AsyncClient, contact_id: str) -> list[dict]:
    return (await card(client, contact_id))["visa_history"]


async def test_a_visa_granted_in_a_case_is_in_the_clients_visa_history(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"], purpose_id=await tourism(client))).json()
    assert await history(client, contact["id"]) == []

    await grant_visa(client, case["id"])
    today = workspace_today()
    opened = await card(client, contact["id"])
    assert opened["visa_history"] == [
        {
            "kind": "schengen",
            "country": "PL",
            "purpose": "tourism",
            "valid_from": str(today - timedelta(days=30)),
            "valid_to": str(today + timedelta(days=335)),
            "note": None,
            "status": "active",
            "case_id": case["id"],
            "case_number": case["number"],
        }
    ]
    # Counted when the card opens: nothing is written into the questionnaire itself.
    assert opened["questionnaire"]["visas"] == []


async def test_the_mark_turns_on_the_day_after_the_last_one_in_the_workspace(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    today = workspace_today()
    [last_day] = (await open_case(client, contact["id"])).json()
    [ended] = (await open_case(client, contact["id"], destination="LT")).json()
    start = str(today - timedelta(days=90))
    await grant_visa(client, last_day["id"], visa_valid_from=start, visa_valid_to=str(today))
    await grant_visa(
        client,
        ended["id"],
        visa_valid_from=start,
        visa_valid_to=str(today - timedelta(days=1)),
    )
    lines = await history(client, contact["id"])
    assert [(v["country"], v["status"]) for v in lines] == [("PL", "active"), ("LT", "expired")]


async def test_a_refusal_and_a_case_not_filed_are_not_visas(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [refused] = (await open_case(client, contact["id"], destination="FR")).json()
    [declined] = (await open_case(client, contact["id"], destination="LT")).json()
    await open_case(client, contact["id"], destination="IT")  # still in work
    await client.patch(f"/api/cases/{refused['id']}", json={"outcome": "refusal"})
    await client.patch(f"/api/cases/{declined['id']}", json={"outcome": "not_submitted"})
    opened = await card(client, contact["id"])
    assert [c["outcome"] for c in opened["cases"]] == [None, "not_submitted", "refusal"]
    assert opened["visa_history"] == []


async def test_changing_the_result_changes_the_history_at_once(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}"
    await grant_visa(client, case["id"])
    assert [v["valid_to"] for v in await history(client, contact["id"])] == [
        str(workspace_today() + timedelta(days=335))
    ]

    corrected = str(workspace_today() + timedelta(days=700))
    assert (await client.patch(url, json={"visa_valid_to": corrected})).status_code == 200
    assert [v["valid_to"] for v in await history(client, contact["id"])] == [corrected]

    assert (await client.patch(url, json={"outcome": "refusal"})).status_code == 200
    assert await history(client, contact["id"]) == []

    await grant_visa(client, case["id"])
    assert len(await history(client, contact["id"])) == 1
    # The result taken back: the case is in work again.
    assert (await client.patch(url, json={"outcome": None})).status_code == 200
    assert await history(client, contact["id"]) == []


async def test_each_member_of_a_family_has_their_own_history(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    anna, sergey, maria = await family_of_three(client)
    await grant_visa(client, anna["id"])
    await client.patch(f"/api/cases/{sergey['id']}", json={"outcome": "refusal"})
    assert [v["case_number"] for v in await history(client, anna["contact_id"])] == [anna["number"]]
    assert await history(client, sergey["contact_id"]) == []
    assert await history(client, maria["contact_id"]) == []


async def test_a_visa_written_by_hand_is_kept_and_not_shown_twice(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    today = workspace_today()
    term = {
        "valid_from": str(today - timedelta(days=30)),
        "valid_to": str(today + timedelta(days=335)),
    }
    written = [
        {"kind": "uk", "country": "GB", "valid_to": "2024-05-01", "note": "гостевая"},
        {"kind": "schengen", "country": "PL", "note": "через регистратора", **term},
    ]
    saved = await client.put(
        f"/api/contacts/{contact['id']}/questionnaire", json={"visas": written}
    )
    assert saved.status_code == 200, saved.text
    lines = await history(client, contact["id"])
    assert [(v["country"], v["case_number"]) for v in lines] == [("GB", None), ("PL", None)]

    await grant_visa(client, case["id"])
    opened = await card(client, contact["id"])
    lines = opened["visa_history"]
    assert [(v["country"], v["case_number"]) for v in lines] == [
        ("GB", None),
        ("PL", case["number"]),
    ]
    assert (lines[0]["status"], lines[0]["note"]) == ("expired", "гостевая")
    assert lines[1]["note"] == "через регистратора"
    # Both records are still in the questionnaire, as she wrote them.
    assert [v["country"] for v in opened["questionnaire"]["visas"]] == ["GB", "PL"]


async def test_another_specialist_sees_none_of_it(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    await grant_visa(client, case["id"])
    assert len(await history(client, contact["id"])) == 1
    client.cookies.clear()

    await as_specialist(client, make_user, email="boris@example.com")
    assert (await client.get(f"/api/contacts/{contact['id']}")).status_code == 404
    # Her own client of the same name has no visas of somebody else's cases.
    namesake = await new_contact(client)
    assert await history(client, namesake["id"]) == []


async def test_an_employee_sees_the_visas_of_the_cases_given_to_them(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    contact = await new_contact(client)
    [given] = (await open_case(client, contact["id"])).json()
    [kept] = (await open_case(client, contact["id"], destination="LT")).json()
    await grant_visa(client, given["id"])
    await grant_visa(client, kept["id"])
    written = [{"kind": "usa", "valid_to": "2030-01-01"}]
    await client.put(f"/api/contacts/{contact['id']}/questionnaire", json={"visas": written})
    assert [v["case_number"] for v in await history(client, contact["id"])] == [
        None,
        given["number"],
        kept["number"],
    ]
    client.cookies.clear()

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)

    async def allow(*modules: str) -> None:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET modules = :modules WHERE email = 'olga@example.com'"),
                {"modules": list(modules)},
            )

    await allow("clients", "cases")
    await give_cases(owner_engine, "olga@example.com", given["id"])
    await login(client, "olga@example.com")
    assert [v["case_number"] for v in await history(client, contact["id"])] == [
        None,
        given["number"],
    ]

    # Without the «cases» module a case's number and country are not hers to see: only
    # what is written in the questionnaire.
    await allow("clients")
    opened = await card(client, contact["id"])
    assert opened["cases"] is None
    assert [(v["kind"], v["case_number"]) for v in opened["visa_history"]] == [("usa", None)]

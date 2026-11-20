"""A case opened from someone else's consultation (pilot fixes 2 and 8, task T2).

The daughter came to the consultation, the case is her father's: the link is shown both
ways, the daughter goes with the case in the client index, and a family member who got
into a case from another member's consultation has his own consultation covered.
"""

from datetime import UTC, date, datetime, time, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, give_cases, login, workspace_today
from tests.test_client_index import (
    CONTACTS,
    as_specialist,
    consult,
    contact,
    countries,
    open_case,
    search,
)


async def case_from(
    client: AsyncClient,
    anchor: str,
    consultation: dict,
    applicants: list[dict],
    destination="PL",
    **params,
):
    codes = await countries(client)
    return await client.post(
        f"{CONTACTS}/{anchor}/cases",
        params=params,
        json={
            "consultation_id": consultation["id"],
            "destination_id": codes[destination],
            "submission_country_id": codes["BY"],
            "applicants": applicants,
        },
    )


async def daughter_and_father(client: AsyncClient) -> tuple[str, dict, dict]:
    """Natalia came to the consultation; the case is opened on her father, a new card."""
    natalia = await contact(client, "Наталья", last_name="Наумик")
    consultation = await consult(client, natalia, decision="yes")
    response = await case_from(
        client, natalia, consultation, [{"last_name": "NAUMIK", "first_name": "MIKALAI"}]
    )
    assert response.status_code == 201, response.text
    [father_case] = response.json()
    return natalia, consultation, father_case


def row_of(result: dict, contact_id: str) -> dict:
    [row] = [r for r in result["items"] if r["id"] == contact_id]
    return row


def consistent(counts: dict) -> bool:
    return counts["all"] == sum(v for k, v in counts.items() if k != "all")


async def test_the_daughter_goes_with_her_fathers_case_until_its_result(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    natalia, _, father_case = await daughter_and_father(client)

    result = await search(client)
    assert result["counts"]["work"] == 2 and result["counts"]["archive"] == 0
    assert consistent(result["counts"])
    row = row_of(result, natalia)
    assert row["tab"] == "work"
    # Her row shows the father's case with its stage and whose it is.
    assert row["case"]["id"] == father_case["id"]
    assert row["case"]["stage"] == "waiting"
    assert row["case"]["applicant"] == {
        "contact_id": father_case["contact_id"],
        "full_name": "NAUMIK MIKALAI",
    }
    assert row["cases_in_work"] == 1
    assert natalia in {r["id"] for r in (await search(client, tab="work"))["items"]}
    # The stage filter finds her by the case too.
    assert natalia in {r["id"] for r in (await search(client, stage="waiting"))["items"]}

    await client.patch(f"/api/cases/{father_case['id']}", json={"outcome": "visa"})
    result = await search(client)
    assert result["counts"]["work"] == 0 and result["counts"]["archive"] == 2
    assert consistent(result["counts"])
    assert row_of(result, natalia)["tab"] == "archive"


async def test_the_consultation_lists_the_cases_opened_from_it(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    codes = await countries(client)
    natalia, consultation, father_case = await daughter_and_father(client)

    card = (await client.get(f"{CONTACTS}/{natalia}")).json()
    [shown] = card["consultations"]
    assert shown["id"] == consultation["id"]
    assert shown["cases"] == [
        {
            "id": father_case["id"],
            "number": father_case["number"],
            "contact_id": father_case["contact_id"],
            "full_name": "NAUMIK MIKALAI",
            "destination_id": codes["PL"],
            "purpose_id": None,
            "stage": "waiting",
            "outcome": None,
        }
    ]
    # Her card has no case of her own.
    assert card["cases"] == []


async def test_the_case_shows_the_consultation_it_came_from(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    natalia, consultation, father_case = await daughter_and_father(client)

    source = {
        "consultation_id": consultation["id"],
        "held_on": consultation["held_on"],
        "contact_id": natalia,
        "full_name": "Наумик Наталья",
    }
    assert (await client.get(f"/api/cases/{father_case['id']}")).json()["source"] == source
    father = (await client.get(f"{CONTACTS}/{father_case['contact_id']}")).json()
    assert father["cases"][0]["source"] == source


async def test_the_board_shows_one_card_through_the_daughter(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    natalia, _, father_case = await daughter_and_father(client)
    # Her own consultation's case: no «through».
    own = await contact(client, "Ольга")
    own_case = await case_from(client, own, await consult(client, own), [{"contact_id": own}])

    board = (await client.get("/api/cases/board")).json()
    via = {c["id"]: c["via"] for c in board["cases"]}
    assert via == {
        father_case["id"]: {"contact_id": natalia, "full_name": "Наумик Наталья"},
        own_case.json()[0]["id"]: None,
    }
    assert board["leads"] == []


async def test_a_family_members_consultation_is_covered_by_the_group_case(
    client: AsyncClient, make_user: MakeUser
):
    """Nikita had his own consultation («yes»); his case came from Sofia's consultation in
    one group. His consultation is covered: no «Создать кейс», archive after the result."""
    await as_specialist(client, make_user)
    nikita = await contact(client, "Никита", last_name="Кулик")
    nikitas = await consult(client, nikita, decision="yes")
    sofia = await contact(client, "София", last_name="Якусь")
    response = await case_from(
        client,
        sofia,
        await consult(client, sofia, decision="yes"),
        [{"contact_id": sofia}, {"contact_id": nikita}],
        destination="ES",
    )
    assert response.status_code == 201, response.text
    cases = {c["contact_id"]: c for c in response.json()}

    card = (await client.get(f"{CONTACTS}/{nikita}")).json()
    [shown] = card["consultations"]
    assert shown["id"] == nikitas["id"]
    assert [c["id"] for c in shown["cases"]] == [cases[nikita]["id"]]

    for c in cases.values():
        r = await client.patch(f"/api/cases/{c['id']}", json={"outcome": "visa"})
        assert r.status_code == 200, r.text
    result = await search(client)
    assert row_of(result, nikita)["tab"] == "archive"
    assert result["counts"]["consult"] == 0 and consistent(result["counts"])


async def test_an_old_case_of_ones_own_does_not_cover_a_new_consultation(
    client: AsyncClient, make_user: MakeUser
):
    """A returning client: the case of an earlier consultation is not the new one's."""
    await as_specialist(client, make_user)
    anna = await contact(client, "Анна")
    earlier = (workspace_today() - timedelta(days=200)).isoformat()
    old = await case_from(
        client, anna, await consult(client, anna, held_on=earlier), [{"contact_id": anna}]
    )
    await client.patch(f"/api/cases/{old.json()[0]['id']}", json={"outcome": "visa"})
    new = await consult(client, anna, decision="yes")

    assert row_of(await search(client), anna)["tab"] == "consult"
    card = (await client.get(f"{CONTACTS}/{anna}")).json()
    assert {c["id"]: c["cases"] for c in card["consultations"]}[new["id"]] == []


async def test_a_case_without_a_consultation_covers_the_one_before_it(
    client: AsyncClient, make_user: MakeUser
):
    """«+ Кейс» without picking the consultation after a «yes»: the case is that one's."""
    await as_specialist(client, make_user)
    anna = await contact(client, "Анна")
    consultation = await consult(client, anna, decision="yes")
    visa_case = await open_case(client, anna)
    card = (await client.get(f"{CONTACTS}/{anna}")).json()
    assert {c["id"]: [x["id"] for x in c["cases"]] for c in card["consultations"]} == {
        consultation["id"]: [visa_case["id"]]
    }


async def test_a_second_case_for_someone_with_a_case_in_work_is_confirmed(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    codes = await countries(client)
    nikita = await contact(client, "Никита", last_name="Кулик")
    first = await open_case(client, nikita, destination="ES")
    body = {
        "destination_id": codes["PL"],
        "submission_country_id": codes["BY"],
        "applicants": [{"contact_id": nikita}, {"first_name": "Новый"}],
    }

    asked = await client.post(f"{CONTACTS}/{nikita}/cases", json=body)
    assert asked.status_code == 409
    assert asked.json() == {
        "detail": "case_in_work",
        "cases": [
            {
                "contact_id": nikita,
                "full_name": "Кулик Никита",
                "number": first["number"],
                "destination_id": codes["ES"],
            }
        ],
    }
    # Nothing was created: no case, no new card.
    assert (await search(client))["counts"]["all"] == 1

    confirmed = await client.post(
        f"{CONTACTS}/{nikita}/cases", params={"allow_case_in_work": "true"}, json=body
    )
    assert confirmed.status_code == 201, confirmed.text
    # A case with a result does not ask.
    await client.patch(f"/api/cases/{first['id']}", json={"outcome": "visa"})
    for c in confirmed.json():
        await client.patch(f"/api/cases/{c['id']}", json={"outcome": "visa"})
    again = await client.post(
        f"{CONTACTS}/{nikita}/cases", json={**body, "applicants": [body["applicants"][0]]}
    )
    assert again.status_code == 201, again.text


async def test_links_show_an_employee_only_what_is_theirs(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The daughter's card is the employee's (she made it), the father's case is not
    given to her: neither link names what she does not see."""
    tenant_id = await as_specialist(client, make_user)
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'olga@example.com'")
        )
    client.cookies.clear()
    await login(client, "olga@example.com")
    natalia = await contact(client, "Наталья", last_name="Наумик")
    consultation = await consult(client, natalia, decision="yes")
    client.cookies.clear()
    await login(client, "anna@example.com")
    response = await case_from(
        client, natalia, consultation, [{"last_name": "NAUMIK", "first_name": "MIKALAI"}]
    )
    assert response.status_code == 201, response.text
    [father_case] = response.json()
    other = await contact(client, "Чужая")
    given = await case_from(
        client,
        other,
        await consult(client, other),
        [{"last_name": "GIVEN", "first_name": "CASE"}],
    )
    [given_case] = given.json()

    client.cookies.clear()
    await login(client, "olga@example.com")
    card = await client.get(f"{CONTACTS}/{natalia}")
    assert card.status_code == 200
    assert card.json()["consultations"][0]["cases"] == []
    assert "NAUMIK" not in card.text
    found = await client.post(f"{CONTACTS}/search", json={})
    assert "NAUMIK" not in found.text
    assert row_of(found.json(), natalia)["case"] is None
    assert (await client.get(f"/api/cases/{father_case['id']}")).status_code == 404

    # Given the case of the other's father, she does not learn whose consultation it was.
    await give_cases(owner_engine, "olga@example.com", given_case["id"])
    shown = await client.get(f"/api/cases/{given_case['id']}")
    assert shown.status_code == 200
    assert shown.json()["source"] is None
    assert "Чужая" not in shown.text
    board = await client.get("/api/cases/board")
    assert {c["id"]: c["via"] for c in board.json()["cases"]} == {given_case["id"]: None}


async def test_links_stay_in_their_workspace(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    natalia, consultation, father_case = await daughter_and_father(client)
    client.cookies.clear()
    await as_specialist(client, make_user, "boris@example.com")
    mine = await contact(client, "Борис")
    # A case cannot be opened from another workspace's consultation…
    response = await case_from(client, mine, consultation, [{"contact_id": mine}])
    assert response.status_code == 404
    # …and their cases neither show on the board nor move this workspace's tabs.
    assert (await client.get("/api/cases/board")).json()["cases"] == []
    result = await search(client)
    assert result["counts"] == {"all": 1, "work": 0, "consult": 1, "thinking": 0, "archive": 0}
    assert (await client.get(f"{CONTACTS}/{natalia}")).status_code == 404
    assert (await client.get(f"/api/cases/{father_case['id']}")).status_code == 404


async def test_the_day_a_case_was_opened_is_the_specialists_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Opened at 01:30 in Minsk on the consultation's day (22:30 UTC the day before): the
    same day, so it covers the consultation; a day earlier, it does not."""
    await as_specialist(client, make_user)
    anna = await contact(client, "Анна")
    consultation = await consult(client, anna, decision="yes")
    visa_case = await open_case(client, anna)

    async def opened_at(moment: datetime) -> list[list[str]]:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE visa_cases SET created_at = :at WHERE id = :id"),
                {"at": moment, "id": visa_case["id"]},
            )
        card = (await client.get(f"{CONTACTS}/{anna}")).json()
        return [[x["id"] for x in c["cases"]] for c in card["consultations"]]

    held_on = date.fromisoformat(consultation["held_on"])
    midnight = datetime.combine(held_on, time(0, 0), tzinfo=UTC)
    assert await opened_at(midnight - timedelta(hours=1, minutes=30)) == [[visa_case["id"]]]
    assert await opened_at(midnight - timedelta(hours=3, minutes=30)) == [[]]

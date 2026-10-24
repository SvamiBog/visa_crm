from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

CONTACTS = "/api/contacts"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def countries(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/reference/countries")
    if response.status_code == 403:
        response = await client.get("/api/admin/reference/countries")
    return {c["code"]: c["id"] for c in response.json()}


async def new_contact(client: AsyncClient, **fields) -> dict:
    response = await client.post(
        CONTACTS, json={"last_name": "Ковалёва", "first_name": "Анна", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def open_case(client: AsyncClient, contact_id: str, destination="PL", **fields):
    codes = await countries(client)
    body = {
        "destination_id": codes[destination],
        "submission_country_id": codes["BY"],
        "applicants": [{"contact_id": contact_id}],
        **fields,
    }
    return await client.post(f"{CONTACTS}/{contact_id}/cases", json=body)


async def card(client: AsyncClient, contact_id: str) -> dict:
    return (await client.get(f"{CONTACTS}/{contact_id}")).json()


async def as_admin(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")


async def admin_rule(client: AsyncClient, destination: str, **changes) -> dict:
    """As the admin: change the rule «destination × Belarus»; returns it."""
    codes = await countries(client)
    rules = (await client.get("/api/admin/reference/rules")).json()
    rule = next(
        r
        for r in rules
        if r["destination_id"] == codes[destination] and r["submission_id"] == codes["BY"]
    )
    if changes:
        response = await client.patch(f"/api/admin/reference/rules/{rule['id']}", json=changes)
        assert response.status_code == 200, response.text
        rule = response.json()
    return rule


async def add_city(client: AsyncClient, rule_id: str, city_ru: str, city_en: str) -> str:
    response = await client.post(
        f"/api/admin/reference/rules/{rule_id}/visa-centers",
        json={"city_ru": city_ru, "city_en": city_en},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_case_for_one_applicant(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    purpose = (await client.post("/api/dictionaries/purposes", json={"name": "Туризм"})).json()
    response = await open_case(client, contact["id"], purpose_id=purpose["id"], hall="standard")
    assert response.status_code == 201, response.text
    [case] = response.json()
    assert case["number"] == 1
    assert case["stage"] == "waiting"
    assert case["group_id"] is None and case["group"] == []
    assert [c["id"] for c in (await card(client, contact["id"]))["cases"]] == [case["id"]]

    # Numbers go on within the workspace.
    [second] = (await open_case(client, contact["id"], destination="LT")).json()
    assert second["number"] == 2


async def test_numbers_are_per_workspace(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    await open_case(client, (await new_contact(client))["id"])
    client.cookies.clear()
    await as_specialist(client, make_user, "bella@example.com")
    [case] = (await open_case(client, (await new_contact(client))["id"])).json()
    assert case["number"] == 1


async def test_create_cases_from_consultation(client: AsyncClient, make_user: MakeUser):
    """«Создать кейс»: a case for every applicant, joined in a submission group."""
    await as_specialist(client, make_user)
    channel = (await client.post("/api/dictionaries/channels", json={"name": "Таргет"})).json()
    anna = await new_contact(client, lead_channel_id=channel["id"])
    maria = await new_contact(client, first_name="Мария")
    consultation = (
        await client.post(
            f"{CONTACTS}/{anna['id']}/consultations",
            json={"held_on": "2026-10-10", "applicants": 3, "decision": "thinking"},
        )
    ).json()
    codes = await countries(client)
    response = await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json={
            "consultation_id": consultation["id"],
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [
                {"contact_id": anna["id"]},
                {"last_name": " Ковалёв ", "first_name": "Сергей", "label": "муж"},
                {"contact_id": maria["id"]},
            ],
        },
    )
    assert response.status_code == 201, response.text
    cases = response.json()
    assert [c["number"] for c in cases] == [1, 2, 3]
    assert len({c["group_id"] for c in cases}) == 1 and cases[0]["group_id"] is not None
    assert all(c["consultation_id"] == consultation["id"] for c in cases)
    assert [m["full_name"] for m in cases[0]["group"]] == ["Ковалёв Сергей", "Ковалёва Мария"]

    anna_card = await card(client, anna["id"])
    # The client decided «yes» once the cases are open.
    assert anna_card["consultations"][0]["decision"] == "yes"
    # The new family member got a card, linked to Anna, with Anna's channel.
    [link] = anna_card["relations"]
    assert (link["full_name"], link["label"]) == ("Ковалёв Сергей", "муж")
    sergey = (await card(client, link["related_id"]))["contact"]
    assert sergey["lead_channel_id"] == channel["id"]
    assert (sergey["last_name"], sergey["first_name"], sergey["middle_name"]) == (
        "Ковалёв",
        "Сергей",
        None,
    )
    assert [c["number"] for c in (await card(client, sergey["id"]))["cases"]] == [2]


async def test_applicants_are_checked(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    codes = await countries(client)
    url = f"{CONTACTS}/{anna['id']}/cases"
    base = {"destination_id": codes["PL"], "submission_country_id": codes["BY"]}
    twice = await client.post(
        url, json={**base, "applicants": [{"contact_id": anna["id"]}, {"contact_id": anna["id"]}]}
    )
    assert twice.status_code == 422 and twice.json()["detail"] == "duplicate_applicant"
    both = await client.post(
        url, json={**base, "applicants": [{"contact_id": anna["id"], "first_name": "Сергей"}]}
    )
    assert both.status_code == 422
    # A new applicant is named by the surname or the given name, as a new card.
    for nameless in ({}, {"middle_name": "Сергеевич"}, {"first_name": " ", "label": "сын"}):
        response = await client.post(url, json={**base, "applicants": [nameless]})
        assert response.status_code == 422, nameless
    assert (await client.post(url, json={**base, "applicants": []})).status_code == 422
    same = await client.post(
        url,
        json={
            **base,
            "submission_country_id": codes["PL"],
            "applicants": [{"contact_id": anna["id"]}],
        },
    )
    assert same.status_code == 422 and same.json()["detail"] == "same_country"


async def test_stage_moves_on_with_date_and_result(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}"

    # The appointment date ends the wait: documents are collected after it comes.
    dated = (await client.patch(url, json={"submission_on": "2026-11-20"})).json()
    assert dated["stage"] == "documents"
    # The specialist moves the case by hand, also back: saving the same date again
    # (the edit form sends every field) does not move it on.
    assert (await client.patch(url, json={"stage": "waiting"})).json()["stage"] == "waiting"
    again = await client.patch(url, json={"submission_on": "2026-11-20", "hall": "premium"})
    assert again.json()["stage"] == "waiting"
    assert (await client.patch(url, json={"stage": "review"})).json()["stage"] == "review"

    visa = (
        await client.patch(
            url,
            json={
                "outcome": "visa",
                "decided_on": "2026-12-01",
                "visa_entries": "multiple",
                "visa_valid_from": "2026-12-05",
                "visa_valid_to": "2027-12-04",
                "stay_days": 90,
            },
        )
    ).json()
    assert visa["stage"] == "result"
    assert visa["visa_entries"] == "multiple" and visa["stay_days"] == 90

    # Another outcome drops the visa details.
    refusal = (await client.patch(url, json={"outcome": "refusal"})).json()
    assert refusal["visa_entries"] is None and refusal["visa_valid_to"] is None
    assert refusal["decided_on"] == "2026-12-01"


async def test_result_rules(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}"
    backwards = await client.patch(
        url,
        json={"outcome": "visa", "visa_valid_from": "2027-01-10", "visa_valid_to": "2027-01-01"},
    )
    assert backwards.status_code == 422
    assert (await client.patch(url, json={"decided_on": "2026-12-01"})).status_code == 422
    details_without_visa = await client.patch(url, json={"stay_days": 30})
    assert details_without_visa.status_code == 422
    declined = await client.patch(url, json={"outcome": "not_submitted", "after_appeal": True})
    assert declined.status_code == 422
    # Nothing was saved by the rejected changes.
    assert (await client.get(url)).json()["outcome"] is None


async def test_appeal_deadline_from_the_rules(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    await admin_rule(client, "LT", appeals_accepted=True, appeal_deadline_days=14)
    client.cookies.clear()
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"], destination="LT")).json()
    url = f"/api/cases/{case['id']}"
    refused = (
        await client.patch(url, json={"outcome": "refusal", "decided_on": "2026-10-01"})
    ).json()
    assert refused["appeals_accepted"] is True
    assert refused["appeal_until"] == "2026-10-15"
    # The result after the appeal is final.
    appealed = (await client.patch(url, json={"after_appeal": True})).json()
    assert appealed["appeal_until"] is None


async def test_visa_center_belongs_to_the_pair(client: AsyncClient, make_user: MakeUser):
    await as_admin(client, make_user)
    poland = await admin_rule(client, "PL")
    minsk = await add_city(client, poland["id"], "Минск", "Minsk")
    italy = await admin_rule(client, "IT")
    brest = await add_city(client, italy["id"], "Брест", "Brest")
    client.cookies.clear()

    await as_specialist(client, make_user)
    contact = await new_contact(client)
    wrong = await open_case(client, contact["id"], visa_center_id=brest)
    assert wrong.status_code == 422 and wrong.json()["detail"] == "visa_center_mismatch"
    [case] = (await open_case(client, contact["id"], visa_center_id=minsk)).json()
    assert case["visa_center_id"] == minsk
    # Changing the country without the center keeps the pair consistent.
    codes = await countries(client)
    moved = await client.patch(f"/api/cases/{case['id']}", json={"destination_id": codes["IT"]})
    assert moved.status_code == 422
    moved = await client.patch(
        f"/api/cases/{case['id']}", json={"destination_id": codes["IT"], "visa_center_id": brest}
    )
    assert moved.status_code == 200


async def test_group_join_and_leave(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    sergey = await new_contact(client, last_name="Ковалёв", first_name="Сергей")
    await client.post(
        f"{CONTACTS}/{anna['id']}/relations",
        json={"related_id": sergey["id"], "label": "муж", "reverse_label": "жена"},
    )
    [anna_case] = (await open_case(client, anna["id"])).json()
    [sergey_case] = (await open_case(client, sergey["id"])).json()

    candidates = (await client.get(f"/api/cases/{anna_case['id']}/group-candidates")).json()
    assert [c["case_id"] for c in candidates] == [sergey_case["id"]]

    joined = await client.post(
        f"/api/cases/{anna_case['id']}/group", json={"case_id": sergey_case["id"]}
    )
    assert joined.status_code == 200, joined.text
    assert [m["case_id"] for m in joined.json()["group"]] == [sergey_case["id"]]
    assert (await client.get(f"/api/cases/{anna_case['id']}/group-candidates")).json() == []

    left = await client.delete(f"/api/cases/{sergey_case['id']}/group")
    assert left.status_code == 204
    # A group of one is dissolved.
    assert (await client.get(f"/api/cases/{anna_case['id']}")).json()["group_id"] is None


async def test_groups_are_not_merged(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    codes = await countries(client)
    anna = await new_contact(client)

    async def family(name: str) -> list[dict]:
        response = await client.post(
            f"{CONTACTS}/{anna['id']}/cases",
            json={
                "destination_id": codes["PL"],
                "submission_country_id": codes["BY"],
                "applicants": [{"first_name": f"{name} 1"}, {"first_name": f"{name} 2"}],
            },
        )
        return response.json()

    first, second = await family("А"), await family("Б")
    response = await client.post(
        f"/api/cases/{first[0]['id']}/group", json={"case_id": second[0]["id"]}
    )
    assert response.status_code == 409 and response.json()["detail"] == "in_another_group"


async def test_other_workspaces_cases_are_not_found(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    [case] = (await open_case(client, anna["id"])).json()
    purpose = (await client.post("/api/dictionaries/purposes", json={"name": "Туризм"})).json()
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    own = await new_contact(client)
    [own_case] = (await open_case(client, own["id"])).json()
    assert (await client.get(f"/api/cases/{case['id']}")).status_code == 404
    assert (
        await client.patch(f"/api/cases/{case['id']}", json={"stage": "review"})
    ).status_code == 404
    assert (await open_case(client, anna["id"])).status_code == 404
    # Someone else's contact as an applicant, purpose or case to group with.
    foreign_applicant = await client.post(
        f"{CONTACTS}/{own['id']}/cases",
        json={
            "destination_id": own_case["destination_id"],
            "submission_country_id": own_case["submission_country_id"],
            "applicants": [{"contact_id": anna["id"]}],
        },
    )
    assert foreign_applicant.status_code == 404
    assert (await open_case(client, own["id"], purpose_id=purpose["id"])).status_code == 404
    grouped = await client.post(f"/api/cases/{own_case['id']}/group", json={"case_id": case["id"]})
    assert grouped.status_code == 404


async def test_cases_module_is_required(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    contact = await new_contact(client)
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    # The card without cases, and no way to open one.
    assert (await card(client, contact["id"]))["cases"] is None
    response = await open_case(client, contact["id"])
    assert response.status_code == 403 and response.json()["detail"] == "module_disabled"


async def test_refusal_in_a_case_warns_about_the_same_country(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [refused] = (await open_case(client, contact["id"], destination="FR")).json()
    await client.patch(
        f"/api/cases/{refused['id']}", json={"outcome": "refusal", "decided_on": "2026-06-01"}
    )
    assert (await card(client, contact["id"]))["hints"] == []

    await open_case(client, contact["id"], destination="FR")
    hints = (await card(client, contact["id"]))["hints"]
    assert {"code": "refusal_before", "country": "FR"} in hints


async def test_log_has_ids_only(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    codes = await countries(client)
    await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [
                {"contact_id": anna["id"]},
                {"last_name": "Секретов", "first_name": "Сергей"},
            ],
        },
    )
    events = (await client.get("/api/audit")).json()["events"]
    actions = [e["action"] for e in events]
    assert actions.count("cases.created") == 2 and "contacts.linked" in actions
    assert "Секретов" not in str(events)

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_tenant
from app.models import VisaCase
from tests.helpers import MakeUser, give_cases, login, workspace_today

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


async def own_rule(client: AsyncClient, destination: str, **changes) -> dict:
    """As the specialist: change her direction «destination × Belarus»; returns it."""
    codes = await countries(client)
    rules = (await client.get("/api/reference/rules")).json()
    rule = next(
        r
        for r in rules
        if r["destination_id"] == codes[destination] and r["submission_id"] == codes["BY"]
    )
    if changes:
        response = await client.patch(f"/api/reference/rules/{rule['id']}", json=changes)
        assert response.status_code == 200, response.text
        rule = response.json()
    return rule


async def add_city(client: AsyncClient, rule_id: str, city: str) -> str:
    response = await client.post(
        f"/api/reference/rules/{rule_id}/visa-centers", json={"cities": [city]}
    )
    assert response.status_code == 200, response.text
    return next(c["id"] for c in response.json()["visa_centers"] if c["city_ru"] == city)


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


async def test_the_result_stage_needs_a_result(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """CRM audit 2026-11-18 (owner's decision): a case put in «Результат» without a result
    left the work list for the archive, missed the funnel and the approval rate and never
    brought the appeal or visa-expiry reminder. «Результат» takes a case only with its
    result; a result taken away puts the case back where its dates say."""
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"])).json()
    url = f"/api/cases/{case['id']}"
    move = {"case_ids": [case["id"]], "stage": "result"}

    by_hand = await client.patch(url, json={"stage": "result"})
    assert by_hand.status_code == 422 and by_hand.json()["detail"] == "outcome_required"
    on_board = await client.post("/api/cases/stage", json=move)
    assert on_board.status_code == 422 and on_board.json()["detail"] == "outcome_required"
    cleared_with_it = await client.patch(url, json={"stage": "result", "outcome": None})
    assert cleared_with_it.status_code == 422
    assert (await client.get(url)).json()["stage"] == "waiting"

    # With its result it goes there, also after the specialist moved it back by hand.
    submitted = (workspace_today() - timedelta(days=20)).isoformat()
    visa = await client.patch(
        url, json={"submission_on": submitted, "outcome": "visa", "decided_on": submitted}
    )
    assert visa.json()["stage"] == "result"
    assert (await client.patch(url, json={"stage": "review"})).json()["stage"] == "review"
    assert (await client.post("/api/cases/stage", json=move)).status_code == 204

    # Taken away, the result puts the case back by its dates: submitted — under review.
    reopened = (await client.patch(url, json={"outcome": None})).json()
    assert (reopened["stage"], reopened["decided_on"]) == ("review", None)

    # A case already closed without a result before this rule can still be edited.
    [old] = (await open_case(client, contact["id"], destination="LV")).json()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE visa_cases SET stage = 'result' WHERE id = CAST(:id AS uuid)"),
            {"id": old["id"]},
        )
    edited = await client.patch(f"/api/cases/{old['id']}", json={"hall": "premium"})
    assert edited.status_code == 200 and edited.json()["stage"] == "result"


async def test_a_family_does_not_follow_a_case_into_the_result(
    client: AsyncClient, make_user: MakeUser
):
    """A case with its result goes to «Результат» again by hand; a member without one
    stays where it stood, and the board refuses a family with any case without one."""
    await as_specialist(client, make_user)
    anna, sergey, _ = await family_of_three(client)
    await client.patch(
        f"/api/cases/{anna['id']}", json={"outcome": "visa", "decided_on": "2026-11-01"}
    )
    for c in (anna, sergey):
        await client.patch(f"/api/cases/{c['id']}", json={"stage": "review"})

    response = await client.patch(
        f"/api/cases/{anna['id']}", json={"stage": "result", "apply_to_group": True}
    )
    assert response.status_code == 200, response.text
    assert (await client.get(f"/api/cases/{sergey['id']}")).json()["stage"] == "review"

    both = {"case_ids": [anna["id"], sergey["id"]], "stage": "result"}
    refused = await client.post("/api/cases/stage", json=both)
    assert refused.status_code == 422 and refused.json()["detail"] == "outcome_required"


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


async def test_result_rules_hold_for_a_case_with_a_city(client: AsyncClient, make_user: MakeUser):
    """The city of a case is looked up in the database; a wrong result must be refused by
    the rule before that, not by the table's check on the way."""
    await as_specialist(client, make_user)
    minsk = await add_city(client, (await own_rule(client, "PL"))["id"], "Минск")
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"], visa_center_id=minsk)).json()
    url = f"/api/cases/{case['id']}"
    backwards = await client.patch(
        url,
        json={"outcome": "visa", "visa_valid_from": "2027-01-10", "visa_valid_to": "2027-01-01"},
    )
    assert backwards.status_code == 422 and backwards.json()["detail"] == "validation"
    undecided = await client.patch(url, json={"decided_on": "2026-12-01"})
    assert undecided.status_code == 422 and undecided.json()["detail"] == "validation"
    assert (await client.get(url)).json()["outcome"] is None


async def test_appeal_deadline_from_the_rules(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    await own_rule(client, "LT", appeals_accepted=True, appeal_deadline_days=14)
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
    await as_specialist(client, make_user)
    minsk = await add_city(client, (await own_rule(client, "PL"))["id"], "Минск")
    brest = await add_city(client, (await own_rule(client, "IT"))["id"], "Брест")
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


async def test_direction_in_use_is_switched_off_not_deleted(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    poland = await own_rule(client, "PL")
    minsk = await add_city(client, poland["id"], "Минск")
    contact = await new_contact(client)
    [case] = (await open_case(client, contact["id"], visa_center_id=minsk)).json()

    city = await client.delete(f"/api/reference/visa-centers/{minsk}")
    assert city.status_code == 409 and city.json()["detail"] == "visa_center_in_use"
    rule = await client.delete(f"/api/reference/rules/{poland['id']}")
    assert rule.status_code == 409 and rule.json()["detail"] == "rule_in_use"

    # Switched off: no longer offered, the case keeps its city.
    off = await client.patch(f"/api/reference/visa-centers/{minsk}", json={"is_active": False})
    assert off.status_code == 200
    assert (await own_rule(client, "PL"))["visa_centers"] == []
    assert (await client.get(f"/api/cases/{case['id']}")).json()["visa_center_id"] == minsk


async def test_case_cannot_take_another_workspaces_city(
    client: AsyncClient, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
):
    await as_specialist(client, make_user)
    foreign = await add_city(client, (await own_rule(client, "PL"))["id"], "Минск")
    client.cookies.clear()

    bella = await as_specialist(client, make_user, "bella@example.com")
    contact = await new_contact(client)
    # Someone else's city is simply not found, like any other foreign id.
    assert (await open_case(client, contact["id"], visa_center_id=foreign)).status_code == 404
    [case] = (await open_case(client, contact["id"])).json()
    # Not even a direct write in her own scope: the city must be of the same workspace.
    async with sessionmaker() as session:
        scope_to_tenant(session, bella)
        with pytest.raises(IntegrityError):
            await session.execute(
                update(VisaCase).where(VisaCase.id == case["id"]).values(visa_center_id=foreign)
            )


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


async def family_of_three(client: AsyncClient) -> list[dict]:
    """Anna, her husband and her daughter filed together: one case each, in one group."""
    anna = await new_contact(client)
    codes = await countries(client)
    response = await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json={
            "destination_id": codes["PL"],
            "submission_country_id": codes["BY"],
            "applicants": [
                {"contact_id": anna["id"]},
                {"first_name": "Сергей"},
                {"first_name": "Мария"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_editing_one_case_changes_the_whole_family(client: AsyncClient, make_user: MakeUser):
    """Owner's decision 2026-11-15 on the specialist's feedback: a family is filed
    together, so one edit changes the whole group, apart from the result."""
    await as_specialist(client, make_user)
    anna, sergey, maria = await family_of_three(client)
    purpose = (await client.post("/api/dictionaries/purposes", json={"name": "Туризм"})).json()
    codes = await countries(client)
    # Maria already has her result: her case is over and is left as it is.
    await client.patch(
        f"/api/cases/{maria['id']}", json={"outcome": "refusal", "decided_on": "2026-10-01"}
    )

    response = await client.patch(
        f"/api/cases/{anna['id']}",
        json={
            "destination_id": codes["LT"],
            "submission_country_id": codes["BY"],
            "purpose_id": purpose["id"],
            "hall": "premium",
            "submission_on": "2026-12-01",
            "questionnaire_due_on": "2026-11-20",
            "questionnaire_received_on": "2026-11-18",
            "apply_to_group": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "documents"
    husband = (await client.get(f"/api/cases/{sergey['id']}")).json()
    assert husband["destination_id"] == codes["LT"]
    assert husband["purpose_id"] == purpose["id"] and husband["hall"] == "premium"
    assert husband["submission_on"] == "2026-12-01"
    assert husband["questionnaire_due_on"] == "2026-11-20"
    assert husband["stage"] == "documents"
    # Who sent the questionnaire is each one's own.
    assert husband["questionnaire_received_on"] is None
    daughter = (await client.get(f"/api/cases/{maria['id']}")).json()
    assert daughter["destination_id"] == codes["PL"] and daughter["purpose_id"] is None
    assert daughter["stage"] == "result"

    # A result entered in one case stays its own and moves nobody.
    await client.patch(
        f"/api/cases/{anna['id']}",
        json={"outcome": "visa", "decided_on": "2026-12-10", "apply_to_group": True},
    )
    husband = (await client.get(f"/api/cases/{sergey['id']}")).json()
    assert husband["outcome"] is None and husband["stage"] == "documents"

    events = (await client.get("/api/audit")).json()["events"]
    assert any(e["action"] == "cases.updated" and e["target_id"] == sergey["id"] for e in events)


async def test_without_the_mark_only_this_case_changes(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna, sergey, _ = await family_of_three(client)
    response = await client.patch(f"/api/cases/{anna['id']}", json={"hall": "premium"})
    assert response.status_code == 200, response.text
    assert (await client.get(f"/api/cases/{sergey['id']}")).json()["hall"] is None


async def test_a_stage_button_moves_the_family_standing_with_the_case(
    client: AsyncClient, make_user: MakeUser
):
    """As on the board: members at the case's stage move with it, the others stay."""
    await as_specialist(client, make_user)
    anna, sergey, maria = await family_of_three(client)
    await client.patch(f"/api/cases/{maria['id']}", json={"stage": "review"})

    response = await client.patch(
        f"/api/cases/{anna['id']}", json={"stage": "documents", "apply_to_group": True}
    )
    assert response.status_code == 200, response.text
    stages = [
        (await client.get(f"/api/cases/{c['id']}")).json()["stage"] for c in (anna, sergey, maria)
    ]
    assert stages == ["documents", "documents", "review"]


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
    [case] = (await open_case(client, contact["id"])).json()
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await give_cases(owner_engine, "olga@example.com", case["id"])
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


async def test_the_card_opens_with_a_refusal_of_any_year(client: AsyncClient, make_user: MakeUser):
    """The questionnaire lists refusals from 1990 on; a case's refusal decided before that
    (an old one, or a year typed wrong) still warns, and the card still opens."""
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    [refused] = (await open_case(client, contact["id"], destination="FR")).json()
    saved = await client.patch(
        f"/api/cases/{refused['id']}", json={"outcome": "refusal", "decided_on": "1985-06-01"}
    )
    assert saved.status_code == 200, saved.text
    await open_case(client, contact["id"], destination="FR")
    opened = await client.get(f"{CONTACTS}/{contact['id']}")
    assert opened.status_code == 200, opened.text
    assert {"code": "refusal_before", "country": "FR"} in opened.json()["hints"]


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


async def test_a_case_filed_again_is_linked_to_the_previous_one(
    client: AsyncClient, make_user: MakeUser
):
    """After a refusal the specialist files again: a new case linked to the previous one
    (docs/clients-module.md → «Этапы кейса»; specialist run before the pilot, 2026-11-04)."""
    await as_specialist(client, make_user)
    anna = await new_contact(client)
    old = (await open_case(client, anna["id"])).json()[0]
    await client.patch(
        f"/api/cases/{old['id']}", json={"outcome": "refusal", "decided_on": "2026-10-01"}
    )
    codes = await countries(client)
    body = {
        "destination_id": codes["PL"],
        "submission_country_id": codes["BY"],
        "previous_case_id": old["id"],
    }

    again = await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json=body | {"applicants": [{"contact_id": anna["id"]}, {"first_name": "Сергей"}]},
    )
    assert again.status_code == 201, again.text
    mine, husband = again.json()
    assert mine["previous_case_id"] == old["id"] and husband["previous_case_id"] is None
    shown = (await client.get(f"/api/cases/{mine['id']}")).json()
    assert shown["previous_case_id"] == old["id"]
    events = (await client.get("/api/audit")).json()["events"]
    created = next(
        e for e in events if e["action"] == "cases.created" and e["target_id"] == mine["id"]
    )
    assert created["details"]["previous"] == old["id"]

    # The previous case must be one of an applicant's.
    boris = await new_contact(client, first_name="Борис")
    foreign = await client.post(
        f"{CONTACTS}/{boris['id']}/cases",
        json=body | {"applicants": [{"contact_id": boris["id"]}]},
    )
    assert foreign.status_code == 422
    unknown = await client.post(
        f"{CONTACTS}/{anna['id']}/cases",
        json=body
        | {
            "previous_case_id": "00000000-0000-0000-0000-000000000000",
            "applicants": [{"contact_id": anna["id"]}],
        },
    )
    assert unknown.status_code == 404

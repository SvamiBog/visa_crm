from datetime import timedelta

from httpx import AsyncClient

from tests.helpers import MakeUser, login, workspace_today

CONTACTS = "/api/contacts"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def country_id(client: AsyncClient, code: str) -> str:
    countries = (await client.get("/api/reference/countries")).json()
    return next(c["id"] for c in countries if c["code"] == code)


async def new_contact(client: AsyncClient, **fields) -> dict:
    response = await client.post(CONTACTS, json={"first_name": "Анна", **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def consult(client: AsyncClient, contact_id: str, **fields):
    body = {"held_on": "2026-10-10", **fields}
    return await client.post(f"{CONTACTS}/{contact_id}/consultations", json=body)


async def card(client: AsyncClient, contact_id: str) -> dict:
    return (await client.get(f"{CONTACTS}/{contact_id}")).json()


async def test_consultation_with_decision_trip_and_fee(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    channel = (await client.post("/api/dictionaries/channels", json={"name": "Instagram"})).json()
    contact = await new_contact(client, lead_channel_id=channel["id"])
    poland, france = await country_id(client, "PL"), await country_id(client, "FR")

    response = await consult(
        client,
        contact["id"],
        applicants=3,
        decision="thinking",
        destination_ids=[poland, france],
        trip_planned_on="2027-03-01",
        fee_amount="50",
        fee_currency="BYN",
        fee_paid=True,
        fee_paid_on="2026-10-10",
        notes="Едут втроём, Польша или Франция.",
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["fee_amount"] == "50.00"
    # The channel comes from the contact unless another is given.
    assert created["lead_channel_id"] == channel["id"]
    assert created["month"] == "2026-10"

    later = (await consult(client, contact["id"], held_on="2026-10-12")).json()
    listed = (await card(client, contact["id"]))["consultations"]
    assert [c["id"] for c in listed] == [later["id"], created["id"]]  # newest first


async def test_decision_is_changed_later(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    created = (await consult(client, contact["id"], decision="thinking")).json()
    response = await client.patch(f"/api/consultations/{created['id']}", json={"decision": "yes"})
    assert response.status_code == 200
    assert response.json()["decision"] == "yes"


async def test_fee_rules(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    no_currency = await consult(client, contact["id"], fee_amount="50")
    assert no_currency.status_code == 422
    paid_on_unpaid = await consult(
        client, contact["id"], fee_amount="50", fee_currency="BYN", fee_paid_on="2026-10-10"
    )
    assert paid_on_unpaid.status_code == 422

    # Only currencies the specialist accepts (Lists → Currencies).
    await client.patch("/api/dictionaries/money", json={"payment_currencies": ["USD"]})
    pln = await consult(client, contact["id"], fee_amount="50", fee_currency="PLN")
    assert pln.status_code == 422 and pln.json()["detail"] == "currency_not_accepted"


async def test_unknown_or_foreign_ids(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    unknown = await consult(
        client, contact["id"], destination_ids=["00000000-0000-0000-0000-000000000000"]
    )
    assert unknown.status_code == 404
    created = (await consult(client, contact["id"])).json()
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await consult(client, contact["id"])).status_code == 404
    patch = await client.patch(f"/api/consultations/{created['id']}", json={"decision": "no"})
    assert patch.status_code == 404


async def test_questionnaire_is_kept_on_the_contact(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    answers = {
        "passport_issued_on": "2017-02-16",
        "passport_expires_on": "2027-02-15",
        "visas": [{"kind": "schengen", "country": "PL", "valid_to": "2025-08-01"}],
        "refusals": [{"country": "FR", "year": 2024, "reason": "цель поездки"}],
        "relatives_abroad": ["IT"],
    }
    response = await client.put(f"{CONTACTS}/{contact['id']}/questionnaire", json=answers)
    assert response.status_code == 200, response.text
    saved = (await card(client, contact["id"]))["questionnaire"]
    assert saved["refusals"][0]["country"] == "FR"
    assert saved["deportation"] is False

    bad = await client.put(
        f"{CONTACTS}/{contact['id']}/questionnaire", json={"refusals": [{"country": "Франция"}]}
    )
    assert bad.status_code == 422


async def test_hints_use_the_latest_consultation(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    await client.put(
        f"{CONTACTS}/{contact['id']}/questionnaire",
        json={
            "passport_issued_on": "2017-02-16",
            "passport_expires_on": "2027-02-15",
            "refusals": [{"country": "FR"}],
        },
    )
    poland, france, uk = (
        await country_id(client, "PL"),
        await country_id(client, "FR"),
        await country_id(client, "GB"),
    )
    await consult(client, contact["id"], held_on="2026-09-01", destination_ids=[uk])
    await consult(
        client,
        contact["id"],
        held_on="2026-10-10",
        destination_ids=[poland, france],
        trip_planned_on="2026-12-01",
    )
    hints = [(h["code"], h["country"]) for h in (await card(client, contact["id"]))["hints"]]
    assert hints == [
        ("passport_expires_too_soon", "PL"),
        ("passport_expires_too_soon", "FR"),
        ("refusal_before", "FR"),
    ]


async def test_passport_rules_come_from_the_reference(client: AsyncClient, make_user: MakeUser):
    await make_user("root@example.com", role="admin")
    await login(client, "root@example.com")
    countries = {c["code"]: c for c in (await client.get("/api/admin/reference/countries")).json()}
    assert countries["PL"]["passport_valid_after_months"] == 3
    assert countries["PL"]["passport_max_age_years"] == 10
    assert countries["GB"]["passport_valid_after_months"] is None
    # The admin sets a rule for a country that had none.
    response = await client.patch(
        f"/api/admin/reference/countries/{countries['GB']['id']}",
        json={"passport_valid_after_months": 6},
    )
    assert response.json()["passport_valid_after_months"] == 6
    client.cookies.clear()

    await as_specialist(client, make_user)
    contact = await new_contact(client)
    expires = (workspace_today() + timedelta(days=100)).isoformat()
    await client.put(
        f"{CONTACTS}/{contact['id']}/questionnaire", json={"passport_expires_on": expires}
    )
    await consult(client, contact["id"], destination_ids=[countries["GB"]["id"]])
    hints = [h["code"] for h in (await card(client, contact["id"]))["hints"]]
    assert hints == ["passport_expires_too_soon"]


async def test_log_has_ids_only(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await new_contact(client)
    await consult(client, contact["id"], notes="секретные вводные")
    await client.put(f"{CONTACTS}/{contact['id']}/questionnaire", json={"deportation": True})
    events = (await client.get("/api/audit")).json()["events"]
    actions = [e["action"] for e in events[:2]]
    assert actions == ["contacts.questionnaire_updated", "consultations.created"]
    assert "секрет" not in str(events[:2])

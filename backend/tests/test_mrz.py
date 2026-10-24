"""Reading a passport's machine-readable zone and keeping the passport in the card
(backlog 2.5)."""

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.mrz import check_digit
from tests.helpers import MakeUser, login

TD3 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"


def belarus_passport() -> str:
    def with_check(field: str) -> str:
        return field + str(check_digit(field))

    line2 = (
        with_check("MP1234567")
        + "BLR"
        + with_check("850512")
        + "F"
        + with_check("350101")
        + with_check("3120585A001PB8")
    )
    line2 += str(check_digit(line2[0:10] + line2[13:20] + line2[21:43]))
    return "P<BLRIVANOVA<<ANNA<<<<<<<<<<<<<<<<<<<<<<<<<<\n" + line2


async def as_specialist(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, email="anna@example.com"
) -> uuid.UUID | None:
    tenant_id = await make_user(email)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_append(modules, 'passport_mrz') WHERE id = :t"
            ),
            {"t": tenant_id},
        )
    await login(client, email)
    return tenant_id


async def test_passport_is_read_from_its_zone(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    response = await client.post("/api/mrz/read", json={"text": belarus_passport()})
    assert response.status_code == 200, response.text
    mrz = response.json()
    assert mrz["valid"] and mrz["failed"] == []
    assert mrz["full_name_latin"] == "IVANOVA ANNA"
    assert (mrz["document_number"], mrz["personal_number"]) == ("MP1234567", "3120585A001PB8")
    assert (mrz["nationality"], mrz["nationality_country"]) == ("BLR", "BY")
    assert (mrz["birth_date"], mrz["expiry_date"], mrz["sex"]) == ("1985-05-12", "2035-01-01", "F")
    assert mrz["expired"] is False

    specimen = (await client.post("/api/mrz/read", json={"text": TD3})).json()
    assert specimen["expired"] is True
    assert specimen["issuing_country"] is None

    # Nothing about a person is written to the audit log by reading.
    events = (await client.get("/api/audit")).json()["events"]
    assert [e["action"] for e in events] == ["auth.login"]


async def test_text_without_a_zone_is_reported(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    response = await client.post("/api/mrz/read", json={"text": "no zone here"})
    assert response.status_code == 422 and response.json()["detail"] == "mrz_not_found"
    assert (await client.post("/api/mrz/read", json={"text": ""})).status_code == 422


async def test_passport_fields_are_kept_in_the_questionnaire(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await as_specialist(client, make_user, owner_engine)
    contact = (await client.post("/api/contacts", json={"first_name": "Анна"})).json()
    saved = await client.put(
        f"/api/contacts/{contact['id']}/questionnaire",
        json={
            "passport_number": " mp 1234567 ",
            "passport_expires_on": "2035-01-01",
            "nationality": "BY",
            "sex": "F",
            "personal_number": "3120585a001pb8",
        },
    )
    assert saved.status_code == 200, saved.text
    q = (await client.get(f"/api/contacts/{contact['id']}")).json()["questionnaire"]
    assert (q["passport_number"], q["personal_number"]) == ("MP1234567", "3120585A001PB8")
    assert (q["nationality"], q["sex"]) == ("BY", "F")
    bad = await client.put(
        f"/api/contacts/{contact['id']}/questionnaire", json={"passport_number": "MP-123"}
    )
    assert bad.status_code == 422


async def test_reading_needs_its_module(client: AsyncClient, make_user: MakeUser):
    await make_user("vera@example.com")
    await login(client, "vera@example.com")
    denied = await client.post("/api/mrz/read", json={"text": TD3})
    assert denied.status_code == 403 and denied.json()["detail"] == "module_disabled"

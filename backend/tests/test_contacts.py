import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, give_cases, login
from tests.test_cases import open_case

BASE = "/api/contacts"


async def as_specialist(client: AsyncClient, make_user: MakeUser, email="anna@example.com"):
    tenant_id = await make_user(email)
    await login(client, email)
    return tenant_id


async def create(client: AsyncClient, **fields) -> dict:
    body = {"last_name": "Ковалёва", "first_name": "Анна", "middle_name": "Сергеевна", **fields}
    response = await client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def search(client: AsyncClient, query: str = "") -> list[dict]:
    response = await client.post(f"{BASE}/search", json={"query": query})
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_contact_card_with_normalized_phone(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await create(
        client,
        last_name_latin="KAVALEVA",
        first_name_latin="ANNA",
        phone="+375 (29) 000-12-34",
        telegram="@anna_k_visa",
        messenger="whatsapp",
        messenger_contact="+375290001234",
        email="anna.k@example.com",
        birth_date="1990-10-14",
        consent_signed_on="2026-02-03",
        contract_number="127",
        contract_signed_on="2026-02-03",
    )
    assert contact["phone"] == "+375290001234"
    assert contact["telegram"] == "anna_k_visa"  # stored without "@"

    card = (await client.get(f"{BASE}/{contact['id']}")).json()
    assert card["contact"]["birth_date"] == "1990-10-14"
    assert card["contact"]["contract_number"] == "127"
    assert card["notes"] == [] and card["relations"] == []


async def test_the_name_is_entered_in_parts(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(client, last_name=" Ковалёва ", middle_name="Сергеевна  ")
    assert (anna["last_name"], anna["first_name"], anna["middle_name"]) == (
        "Ковалёва",
        "Анна",
        "Сергеевна",
    )
    assert anna["full_name"] == "Ковалёва Анна Сергеевна"  # what lists show and search finds
    assert [c["id"] for c in await search(client, "анна серг")] == [anna["id"]]

    url = f"{BASE}/{anna['id']}"
    married = (await client.patch(url, json={"last_name": "Семёнова"})).json()
    assert married["full_name"] == "Семёнова Анна Сергеевна"
    no_patronymic = (await client.patch(url, json={"middle_name": " "})).json()
    assert (no_patronymic["middle_name"], no_patronymic["full_name"]) == (None, "Семёнова Анна")
    only_surname = (await client.patch(url, json={"first_name": None})).json()
    assert only_surname["full_name"] == "Семёнова"

    # The surname or the given name stays: a card without a name is refused.
    nameless = await client.patch(url, json={"last_name": None})
    assert (nameless.status_code, nameless.json()["detail"]) == (422, "name_required")
    for body in ({}, {"middle_name": "Сергеевна"}, {"last_name": " ", "first_name": ""}):
        assert (await client.post(BASE, json=body)).status_code == 422
    assert (await client.post(BASE, json={"first_name": "А" * 101})).status_code == 422


async def test_the_latin_name_is_kept_as_in_the_passport(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(client, last_name_latin="KAVALEVA", first_name_latin="ANNA MARIA")
    assert anna["full_name_latin"] == "KAVALEVA ANNA MARIA"
    url = f"{BASE}/{anna['id']}"
    renamed = (await client.patch(url, json={"last_name_latin": "SIAMIONAVA"})).json()
    assert (renamed["first_name_latin"], renamed["full_name_latin"]) == (
        "ANNA MARIA",
        "SIAMIONAVA ANNA MARIA",
    )
    cleared = await client.patch(url, json={"last_name_latin": None, "first_name_latin": ""})
    assert cleared.json()["full_name_latin"] is None
    assert (await client.patch(url, json={"full_name": "Кто-то"})).json()["full_name"] == (
        "Ковалёва Анна Сергеевна"  # the whole name is not taken: it is the parts joined
    )


async def test_the_passport_is_entered_with_the_card(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    passport = {
        "passport_number": " mp 1234567 ",
        "passport_issued_on": "2021-03-04",
        "passport_expires_on": "2031-03-03",
        "passport_issued_by": " Министерство   внутренних дел ",
        "nationality": "BY",
        "sex": "F",
        "personal_number": "3120585a001pb8",
        "birth_place": "г. Минск",
    }
    anna = await create(client, passport=passport)
    url = f"{BASE}/{anna['id']}"
    q = (await client.get(url)).json()["questionnaire"]
    assert (q["passport_number"], q["personal_number"]) == ("MP1234567", "3120585A001PB8")
    assert (q["passport_issued_by"], q["birth_place"]) == (
        "Министерство внутренних дел",
        "г. Минск",
    )
    assert (q["passport_issued_on"], q["passport_expires_on"]) == ("2021-03-04", "2031-03-03")
    assert (q["nationality"], q["sex"], q["visas"]) == ("BY", "F", [])

    # The questionnaire's answers stay; of the passport, only the fields sent change.
    answers = {**q, "deportation": True, "humanitarian_trips": ["IT"]}
    assert (await client.put(f"{url}/questionnaire", json=answers)).status_code == 200
    changed = await client.patch(
        url, json={"passport": {"passport_number": "MP7654321", "birth_place": None}}
    )
    assert changed.status_code == 200, changed.text
    q = (await client.get(url)).json()["questionnaire"]
    assert (q["passport_number"], q["birth_place"]) == ("MP7654321", None)
    assert (q["passport_issued_by"], q["deportation"], q["humanitarian_trips"]) == (
        "Министерство внутренних дел",
        True,
        ["IT"],
    )
    bad = await client.patch(url, json={"passport": {"passport_number": "MP-123"}})
    assert bad.status_code == 422

    # Which passport fields changed is logged, not their values.
    events = (await client.get("/api/audit")).json()["events"]
    updated = [e for e in events if e["action"] == "contacts.updated"]
    assert {"fields": "birth_place,passport_number"} in [e["details"] for e in updated]
    assert all("MP" not in str(e["details"]) for e in events)


async def test_duplicate_phone_points_to_existing_card(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    first = await create(client, phone="+375 29 000-12-34")
    response = await client.post(BASE, json={"first_name": "Анна", "phone": "00375290001234"})
    assert response.status_code == 409
    assert response.json() == {"detail": "phone_taken", "contact_id": first["id"]}

    other = await create(client, last_name=None, first_name="Сергей", phone="+375290009999")
    changed = await client.patch(f"{BASE}/{other['id']}", json={"phone": "+375290001234"})
    assert changed.status_code == 409


@pytest.mark.parametrize("phone", ["123", "+1 234", "телефон"])
async def test_phone_needs_country_code_digits(
    client: AsyncClient, make_user: MakeUser, phone: str
):
    await as_specialist(client, make_user)
    response = await client.post(BASE, json={"first_name": "X", "phone": phone})
    assert response.status_code == 422


async def test_search_by_name_phone_and_telegram(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(client, phone="+375290001234", telegram="anna_k_visa")
    await create(
        client,
        last_name="Семёнов",
        first_name="Игорь",
        middle_name=None,
        last_name_latin="SIAMIONAU",
        first_name_latin="IHAR",
    )

    assert [c["id"] for c in await search(client, "ковалёва")] == [anna["id"]]
    assert [c["id"] for c in await search(client, "0001234")] == [anna["id"]]
    assert [c["id"] for c in await search(client, "29 000-12")] == [anna["id"]]
    assert [c["id"] for c in await search(client, "@ANNA_K")] == [anna["id"]]
    assert [c["full_name"] for c in await search(client, "siamionau")] == ["Семёнов Игорь"]
    assert len(await search(client, "")) == 2


async def test_search_terms_never_go_into_urls(client: AsyncClient, make_user: MakeUser):
    """Names and phones are personal data: search takes them in the body, not the URL."""
    await as_specialist(client, make_user)
    response = await client.get(BASE, params={"q": "Анна"})
    assert response.status_code in (404, 405)  # no GET search exists


async def test_workspaces_do_not_see_each_other(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(client, phone="+375290001234")
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert await search(client, "") == []
    assert (await client.get(f"{BASE}/{anna['id']}")).status_code == 404
    assert (await client.patch(f"{BASE}/{anna['id']}", json={"email": "x@x.io"})).status_code == 404
    # The same phone is fine in another workspace.
    await create(client, phone="+375290001234")


async def test_channel_and_partner_must_be_own(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    channel = (await client.post("/api/dictionaries/channels", json={"name": "Instagram"})).json()
    partner = (await client.post("/api/dictionaries/partners", json={"name": "Ольга"})).json()
    contact = await create(client, lead_channel_id=channel["id"], partner_id=partner["id"])
    assert contact["lead_channel_id"] == channel["id"]
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    response = await client.post(BASE, json={"first_name": "X", "lead_channel_id": channel["id"]})
    assert response.status_code == 404
    response = await client.post(BASE, json={"first_name": "X", "partner_id": partner["id"]})
    assert response.status_code == 404


async def test_notes_timeline(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await create(client)
    url = f"{BASE}/{contact['id']}/notes"
    first = (await client.post(url, json={"body": "Консультация: Польша, туризм"})).json()
    second = (await client.post(url, json={"body": "Думает до пятницы"})).json()
    assert first["author_email"] == "anna@example.com"

    notes = (await client.get(f"{BASE}/{contact['id']}")).json()["notes"]
    assert [n["id"] for n in notes] == [second["id"], first["id"]]  # newest first

    edited = await client.patch(f"{url}/{first['id']}", json={"body": "Польша, туризм, 3 чел."})
    assert edited.json()["edited_at"] is not None
    assert (await client.delete(f"{url}/{second['id']}")).status_code == 204
    notes = (await client.get(f"{BASE}/{contact['id']}")).json()["notes"]
    assert [n["body"] for n in notes] == ["Польша, туризм, 3 чел."]

    assert (await client.post(url, json={"body": "   "})).status_code == 422


async def test_employee_edits_only_own_notes(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    contact = await create(client)
    url = f"{BASE}/{contact['id']}/notes"
    specialist_note = (await client.post(url, json={"body": "от визовика"})).json()
    assert (await open_case(client, contact["id"])).status_code == 201
    client.cookies.clear()

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    own = (await client.post(url, json={"body": "от сотрудника"})).json()
    assert (
        await client.patch(f"{url}/{own['id']}", json={"body": "исправлено"})
    ).status_code == 200
    forbidden = await client.patch(f"{url}/{specialist_note['id']}", json={"body": "x"})
    assert forbidden.status_code == 403
    assert (await client.delete(f"{url}/{specialist_note['id']}")).status_code == 403


async def test_clients_module_is_required(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    client.cookies.clear()
    # An employee without the module does not get in.
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    await login(client, "olga@example.com")
    response = await client.post(f"{BASE}/search", json={"query": ""})
    assert response.status_code == 403 and response.json()["detail"] == "module_disabled"
    client.cookies.clear()
    # Nor does the specialist once the admin switches the module off.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET modules = '{cases}' WHERE id = :t"), {"t": tenant_id}
        )
    await login(client, "anna@example.com")
    assert (await client.post(f"{BASE}/search", json={"query": ""})).status_code == 403


async def test_family_links_show_on_both_cards(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(client)
    sergey = await create(client, last_name="Ковалёв", first_name="Сергей", middle_name=None)
    response = await client.post(
        f"{BASE}/{anna['id']}/relations",
        json={"related_id": sergey["id"], "label": "муж", "reverse_label": "жена"},
    )
    assert response.status_code == 201, response.text

    anna_links = (await client.get(f"{BASE}/{anna['id']}")).json()["relations"]
    sergey_links = (await client.get(f"{BASE}/{sergey['id']}")).json()["relations"]
    assert [(r["full_name"], r["label"]) for r in anna_links] == [("Ковалёв Сергей", "муж")]
    assert [(r["full_name"], r["label"]) for r in sergey_links] == [
        ("Ковалёва Анна Сергеевна", "жена")
    ]
    again = await client.post(f"{BASE}/{anna['id']}/relations", json={"related_id": sergey["id"]})
    assert again.status_code == 409
    self_link = await client.post(f"{BASE}/{anna['id']}/relations", json={"related_id": anna["id"]})
    assert self_link.status_code == 422

    removed = await client.delete(f"{BASE}/{sergey['id']}/relations/{anna['id']}")
    assert removed.status_code == 204
    assert (await client.get(f"{BASE}/{anna['id']}")).json()["relations"] == []


async def test_log_has_ids_but_no_personal_data(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    contact = await create(client, phone="+375290001234")
    await client.post(f"{BASE}/{contact['id']}/notes", json={"body": "секрет"})
    await client.patch(f"{BASE}/{contact['id']}", json={"email": "anna@example.org"})
    events = (await client.get("/api/audit")).json()["events"]
    contact_events = [e for e in events if e["action"].startswith("contacts.")]
    assert [e["action"] for e in contact_events] == [
        "contacts.updated",
        "contacts.note_added",
        "contacts.created",
    ]
    for event in contact_events:
        assert event["target_id"] == contact["id"]
        text_of_event = str(event["details"])
        for secret in ("Ковалёва", "375290001234", "секрет", "anna@example.org"):
            assert secret not in text_of_event
    # Which fields changed is logged, not their values.
    assert contact_events[0]["details"] == {"fields": "email"}

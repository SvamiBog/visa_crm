from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import contacts
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


async def test_search_takes_the_text_as_typed(client: AsyncClient, make_user: MakeUser):
    """«_» and «%» are characters of a nick or a number, not wildcards: «anna_k» does not
    find «annaxk», and «%» alone does not list everyone."""
    await as_specialist(client, make_user)
    anna = await create(client, telegram="anna_k_visa")
    await create(client, last_name="Семёнова", first_name="Анна", telegram="annaxk_visa")

    assert [c["id"] for c in await search(client, "anna_k")] == [anna["id"]]
    assert await search(client, "%") == []
    assert len(await search(client, "_visa")) == 2


async def test_search_terms_never_go_into_urls(client: AsyncClient, make_user: MakeUser):
    """Names and phones are personal data: search takes them in the body, not the URL."""
    await as_specialist(client, make_user)
    response = await client.get(BASE, params={"q": "Анна"})
    assert response.status_code in (404, 405)  # no GET search exists


async def test_search_offset_past_64_bits_is_refused(client: AsyncClient, make_user: MakeUser):
    """The database driver cannot send a larger number: the schema stops it, not the
    database with a server error."""
    await as_specialist(client, make_user)
    response = await client.post(f"{BASE}/search", json={"query": "x", "offset": 2**63})
    assert response.status_code == 422
    response = await client.post(f"{BASE}/search", json={"query": "x", "offset": 2**63 - 1})
    assert response.status_code == 200 and response.json()["items"] == []


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


async def test_a_channel_removed_while_the_card_is_saved_is_not_found(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """The channel was there when checked and gone when the card was saved: the database
    refuses the row, and the answer is the check's, not a server error."""
    await as_specialist(client, make_user)
    check_refs = contacts._check_refs
    checks = []

    async def channel_there_at_first(session, fields) -> None:
        checks.append(fields)
        if len(checks) > 1:
            await check_refs(session, fields)

    monkeypatch.setattr(contacts, "_check_refs", channel_there_at_first)
    response = await client.post(BASE, json={"first_name": "X", "lead_channel_id": str(uuid4())})
    assert response.status_code == 404 and response.json()["detail"] == "not_found"


async def test_telegram_longer_than_its_column_is_refused(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    response = await client.post(BASE, json={"first_name": "X", "telegram": "t" * 65})
    assert response.status_code == 422
    # The «@» in front is not kept, so it does not count.
    contact = await create(client, telegram="@" + "t" * 64)
    assert contact["telegram"] == "t" * 64
    response = await client.patch(f"{BASE}/{contact['id']}", json={"telegram": "t" * 65})
    assert response.status_code == 422


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


# --- Duplicates (specialist run before the pilot, 2026-11-04) ----------------------------


async def test_a_local_belarusian_number_is_the_same_number(
    client: AsyncClient, make_user: MakeUser
):
    """Clients give their number the way they are used to: «8 029 …». It must be stored
    with the country code, caught by the duplicate guard and found by search."""
    await as_specialist(client, make_user)
    anna = await create(client, phone="+375 29 000-12-34")

    response = await client.post(BASE, json={"first_name": "Анна", "phone": "8 029 000 12 34"})
    assert response.status_code == 409
    assert response.json() == {"detail": "phone_taken", "contact_id": anna["id"]}

    sergey = await create(client, last_name=None, first_name="Сергей", phone="029 000-99-99")
    assert sergey["phone"] == "+375290009999"
    assert [c["id"] for c in await search(client, "8 029 000 12")] == [anna["id"]]
    assert [c["id"] for c in await search(client, "029 000-12-34")] == [anna["id"]]


async def test_a_shared_telegram_warns_and_can_be_confirmed(
    client: AsyncClient, make_user: MakeUser
):
    """Owner's decision 2026-11-04: a nick another card has → a warning that points to
    that card; the specialist may still save it (a family can share one account)."""
    await as_specialist(client, make_user)
    anna = await create(client, telegram="anna_k_visa")

    body = {"first_name": "Мария", "telegram": "@Anna_K_Visa"}
    response = await client.post(BASE, json=body)
    assert response.status_code == 409
    assert response.json() == {"detail": "telegram_taken", "contact_id": anna["id"]}
    maria = await client.post(BASE, params={"allow_shared_telegram": True}, json=body)
    assert maria.status_code == 201, maria.text

    sergey = await create(client, last_name=None, first_name="Сергей")
    url = f"{BASE}/{sergey['id']}"
    assert (await client.patch(url, json={"telegram": "anna_k_visa"})).status_code == 409
    confirmed = await client.patch(
        url, params={"allow_shared_telegram": True}, json={"telegram": "anna_k_visa"}
    )
    assert confirmed.status_code == 200, confirmed.text
    # Other changes to a card that already shares the nick are not stopped again.
    assert (await client.patch(url, json={"email": "s@example.com"})).status_code == 200
    assert (await client.patch(url, json={"telegram": "anna_k_visa"})).status_code == 200


async def test_a_telegram_that_is_not_a_nick_is_not_a_duplicate(
    client: AsyncClient, make_user: MakeUser
):
    """The field also takes a name («Анна К.»): two clients may well have the same one."""
    await as_specialist(client, make_user)
    await create(client, telegram="Анна К.")
    response = await client.post(BASE, json={"first_name": "Анна", "telegram": "Анна К."})
    assert response.status_code == 201, response.text


async def test_an_empty_duplicate_card_can_be_deleted(client: AsyncClient, make_user: MakeUser):
    """Owner's decision 2026-11-04: a card made by mistake goes, with its notes, links and
    unpaid consultations; the log keeps the fact by id."""
    await as_specialist(client, make_user)
    anna = await create(client, phone="+375290001234")
    duplicate = await create(client, first_name="Аня", middle_name=None)
    url = f"{BASE}/{duplicate['id']}"
    await client.post(f"{url}/notes", json={"body": "дубль"})
    await client.post(f"{url}/relations", json={"related_id": anna["id"], "label": "дубль"})
    held = await client.post(f"{url}/consultations", json={"held_on": "2026-10-10"})
    assert held.status_code == 201, held.text
    assert (await client.get(url)).json()["can_delete"] is True

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{BASE}/{anna['id']}")).json()["relations"] == []
    events = (await client.get("/api/audit")).json()["events"]
    deleted = [e for e in events if e["action"] == "contacts.deleted"]
    assert [e["target_id"] for e in deleted] == [duplicate["id"]]
    assert "Аня" not in str(deleted[0]["details"])


async def test_a_card_with_client_history_is_not_deleted(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    with_case = await create(client, first_name="Кейс")
    assert (await open_case(client, with_case["id"])).status_code == 201
    paid = await create(client, first_name="Оплата")
    held = await client.post(
        f"{BASE}/{paid['id']}/consultations",
        json={
            "held_on": "2026-10-10",
            "fee_amount": "50",
            "fee_currency": "BYN",
            "fee_paid": True,
            "fee_paid_on": "2026-10-10",
        },
    )
    assert held.status_code == 201, held.text
    with_account = await create(client, first_name="Сейф")
    mail = await client.post(
        "/api/dictionaries/vault-systems", json={"name": "Почта", "kind": "email"}
    )
    assert mail.status_code == 201, mail.text
    account = await client.post(
        "/api/vault/entries",
        json={"system_id": mail.json()["id"], "contact_id": with_account["id"], "login": "x"},
    )
    assert account.status_code == 201, account.text

    for contact in (with_case, paid, with_account):
        url = f"{BASE}/{contact['id']}"
        assert (await client.get(url)).json()["can_delete"] is False
        response = await client.delete(url)
        assert response.status_code == 409
        assert response.json()["detail"] == "contact_has_history"
        assert (await client.get(url)).status_code == 200


async def test_only_the_specialist_deletes_a_card(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant_id = await as_specialist(client, make_user)
    contact = await create(client)
    client.cookies.clear()

    await make_user("olga@example.com", "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients}' WHERE email = 'olga@example.com'")
        )
    await login(client, "olga@example.com")
    mine = await create(client, first_name="Ольгин")
    assert (await client.get(f"{BASE}/{mine['id']}")).json()["can_delete"] is False
    assert (await client.delete(f"{BASE}/{mine['id']}")).status_code == 403
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.delete(f"{BASE}/{contact['id']}")).status_code == 404

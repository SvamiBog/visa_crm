import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import contacts
from app.core import import_jobs, storage
from tests.support.helpers import MakeUser, give_cases, login, workspace_today
from tests.test_cases import open_case
from tests.test_documents import _all_waiting, upload

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
    assert card["notes"] == []


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


@pytest.mark.parametrize("field", ["passport_issued_on", "passport_expires_on"])
@pytest.mark.parametrize("day", ["1800-01-01", "0026-03-04", "9999-12-31"])
async def test_a_passport_date_out_of_the_calendar_is_refused(
    client: AsyncClient, make_user: MakeUser, field: str, day: str
):
    """A year typed wrong in the passport's dates is refused under its field, as every
    other date the CRM takes (app/core/days.py); a card saved before keeps opening."""
    await as_specialist(client, make_user)
    created = await client.post(BASE, json={"first_name": "Анна", "passport": {field: day}})
    assert created.status_code == 422
    assert created.json()["detail"][0]["loc"] == ["body", "passport", field]
    assert await search(client) == []

    anna = await create(client)
    changed = await client.patch(f"{BASE}/{anna['id']}", json={"passport": {field: day}})
    assert changed.status_code == 422
    assert changed.json()["detail"][0]["loc"] == ["body", "passport", field]


async def test_duplicate_phone_points_to_existing_card(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    first = await create(client, phone="+375 29 000-12-34")
    response = await client.post(BASE, json={"first_name": "Анна", "phone": "00375290001234"})
    assert response.status_code == 409
    assert response.json() == {"detail": "phone_taken", "contact_id": first["id"]}

    other = await create(client, last_name=None, first_name="Сергей", phone="+375290009999")
    changed = await client.patch(f"{BASE}/{other['id']}", json={"phone": "+375290001234"})
    assert changed.status_code == 409


@pytest.mark.parametrize("phone", ["123", "+1 234", "телефон", "8 916 123-45-67"])
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


async def _daria(client: AsyncClient) -> dict:
    """Daria and two people who share one of her names each."""
    daria = await create(
        client,
        last_name="Ковальчук",
        first_name="Дарья",
        middle_name="Игоревна",
        last_name_latin="KOVALCHUK",
        first_name_latin="DARIA",
        phone="+375291112233",
        telegram="dasha_k",
    )
    await create(client, last_name="Ковальчук", first_name="Алексей", middle_name=None)
    await create(client, last_name="Лебедева", first_name="Дарья", middle_name="Игоревна")
    return daria


@pytest.mark.parametrize(
    "query",
    [
        "Дарья Ковальчук",  # as copied from Telegram: the given name first
        "Ковальчук Дарья",
        "ковальчук дарья",
        "ДАРЬЯ КОВАЛЬЧУК",
        "Daria Kovalchuk",
        "kovalchuk daria",
        "дар ков",
        "Дарья Игоревна Ковальчук",
        "игоревна ковальчук дарья",
        "  Дарья \t  Ковальчук  ",
        "Daria Ковальчук",  # each word in either spelling of the name
    ],
)
async def test_search_finds_a_name_whatever_the_order_of_its_words(
    client: AsyncClient, make_user: MakeUser, query: str
):
    """Every word of the query is in the name, Cyrillic or Latin, in any order and case
    (backlog A2): «Дарья Ковальчук» finds the card kept as «Ковальчук Дарья Игоревна»."""
    await as_specialist(client, make_user)
    daria = await _daria(client)
    assert [c["id"] for c in await search(client, query)] == [daria["id"]]


@pytest.mark.parametrize(
    "query",
    [
        "Дарья Петрова",  # one word of two is nobody's
        "Дарья Ковальчук Сергеевна",
        "Daria Lebedeva",
        "Алексей Игоревна",  # each word is somebody's, no one has both
        "дар_я ковальчук",  # «_» and «%» stay characters in every word
        "ковальчук %",
    ],
)
async def test_search_needs_every_word_in_the_name(
    client: AsyncClient, make_user: MakeUser, query: str
):
    await as_specialist(client, make_user)
    await _daria(client)
    assert await search(client, query) == []


async def test_search_in_words_keeps_what_one_line_found(client: AsyncClient, make_user: MakeUser):
    """One word, a phone typed with spaces, a nick and a profile link find as before:
    they are looked for as the whole query, not word by word."""
    await as_specialist(client, make_user)
    daria = await _daria(client)
    assert len(await search(client, "ковальчук")) == 2
    assert len(await search(client, "Дарья Игоревна")) == 2
    for query in (
        "8 029 111-22-33",
        "+375 29 111 22 33",
        "29 111 22",
        "@dasha_k",
        "https://t.me/dasha_k",
    ):
        assert [c["id"] for c in await search(client, query)] == [daria["id"]], query

    # Another specialist finds no one by the same words.
    client.cookies.clear()
    await as_specialist(client, make_user, "bella@example.com")
    assert await search(client, "Дарья Ковальчук") == []


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


async def test_clients_are_not_linked_to_each_other(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Owner's decision 2026-11-24 (backlog 1.15.11): no links between clients at all. The
    specialist notes who is who in the notes; a family filed together is a submission
    group. The card has no links, their API is gone, and so is the table with its rows."""
    await as_specialist(client, make_user)
    anna = await create(client)
    sergey = await create(client, last_name="Ковалёв", first_name="Сергей", middle_name=None)

    assert "relations" not in (await client.get(f"{BASE}/{anna['id']}")).json()
    # No such endpoint: 404, or 405 once frontend/dist is built (its page takes any GET).
    gone = (404, 405)
    linked = await client.post(
        f"{BASE}/{anna['id']}/relations",
        json={"related_id": sergey["id"], "label": "муж", "reverse_label": "жена"},
    )
    assert linked.status_code in gone
    unlinked = await client.delete(f"{BASE}/{sergey['id']}/relations/{anna['id']}")
    assert unlinked.status_code in gone
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('public.contact_relations')")) is None


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


@pytest.mark.parametrize(
    ("link", "nick"),
    [
        ("https://t.me/anna_k_visa", "anna_k_visa"),
        ("t.me/Anna_K_Visa", "Anna_K_Visa"),
        ("http://telegram.me/anna_k_visa/", "anna_k_visa"),
    ],
)
async def test_a_telegram_link_is_kept_as_its_nick(
    client: AsyncClient, make_user: MakeUser, link: str, nick: str
):
    """A profile link copied from Telegram is the nick: the card with that nick is found
    and warned about, the chat button works, and the search takes the link too."""
    await as_specialist(client, make_user)
    anna = await create(client, telegram="anna_k_visa")

    body = {"first_name": "Мария", "telegram": link}
    response = await client.post(BASE, json=body)
    assert response.status_code == 409
    assert response.json() == {"detail": "telegram_taken", "contact_id": anna["id"]}
    maria = await client.post(BASE, params={"allow_shared_telegram": True}, json=body)
    assert maria.status_code == 201, maria.text
    assert maria.json()["telegram"] == nick
    found = {c["id"] for c in await search(client, link)}
    assert found == {anna["id"], maria.json()["id"]}


async def test_a_link_that_is_not_a_profile_stays_as_typed(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    contact = await create(client, telegram="t.me/joinchat/AbCdEf")
    assert contact["telegram"] == "t.me/joinchat/AbCdEf"


async def test_a_telegram_that_is_not_a_nick_is_not_a_duplicate(
    client: AsyncClient, make_user: MakeUser
):
    """The field also takes a name («Анна К.»): two clients may well have the same one."""
    await as_specialist(client, make_user)
    await create(client, telegram="Анна К.")
    response = await client.post(BASE, json={"first_name": "Анна", "telegram": "Анна К."})
    assert response.status_code == 201, response.text


# What the question before a deletion names for a card with nothing on it.
NOTHING_GOES = {
    "cases": [],
    "payments": 0,
    "payments_total": "0.00",
    "expenses": 0,
    "talons": 0,
    "partner_payouts": 0,
    "attempts": 0,
    "queue_entries": 0,
    "package_items": 0,
    "bookings": 0,
    "outcomes": 0,
    "employees": 0,
    "terms": 0,
    "consultations": 0,
    "paid_consultations": 0,
    "notes": 0,
    "vault_entries": 0,
    "documents": 0,
    "trips": 0,
    "templates": 0,
    "passport": False,
    "questionnaire": False,
}


async def test_an_empty_duplicate_card_can_be_deleted(client: AsyncClient, make_user: MakeUser):
    """Owner's decision 2026-11-04: a card made by mistake goes, with its notes and
    consultations; the log keeps the fact by id."""
    await as_specialist(client, make_user)
    anna = await create(client, phone="+375290001234")
    duplicate = await create(client, first_name="Аня", middle_name=None)
    url = f"{BASE}/{duplicate['id']}"
    await client.post(f"{url}/notes", json={"body": "дубль"})
    held = await client.post(f"{url}/consultations", json={"held_on": "2026-10-10"})
    assert held.status_code == 201, held.text
    assert (await client.get(url)).json()["can_delete"] is True

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{BASE}/{anna['id']}")).status_code == 200
    events = (await client.get("/api/audit")).json()["events"]
    deleted = [e for e in events if e["action"] == "contacts.deleted"]
    assert [e["target_id"] for e in deleted] == [duplicate["id"]]
    assert "Аня" not in str(deleted[0]["details"])


async def test_the_question_names_what_goes_with_the_card(client: AsyncClient, make_user: MakeUser):
    """Pilot remark 1.15.13: before a card is deleted the question says what goes with
    this card — its consultations, notes, passport data and questionnaire answers."""
    await as_specialist(client, make_user)
    empty = await create(client, first_name="Пустая", middle_name=None)
    assert (await client.get(f"{BASE}/{empty['id']}/deletion")).json() == NOTHING_GOES

    duplicate = await create(
        client, first_name="Аня", middle_name=None, passport={"passport_number": "MP1234567"}
    )
    url = f"{BASE}/{duplicate['id']}"
    for body in ("дубль", "звонила с другого номера"):
        assert (await client.post(f"{url}/notes", json={"body": body})).status_code == 201
    # Unpaid, though it has a fee.
    held = await client.post(
        f"{url}/consultations",
        json={"held_on": "2026-10-10", "fee_amount": "50", "fee_currency": "BYN"},
    )
    assert held.status_code == 201, held.text
    assert (await client.get(f"{url}/deletion")).json() == NOTHING_GOES | {
        "consultations": 1,
        "notes": 2,
        "passport": True,
    }

    answered = await client.put(
        f"{url}/questionnaire",
        json={"passport_number": "MP1234567", "refusals": [{"country": "FR", "year": 2024}]},
    )
    assert answered.status_code == 200, answered.text
    assert (await client.get(f"{url}/deletion")).json()["questionnaire"] is True
    # Answers without the passport: the passport data page is empty.
    only_answers = await create(client, first_name="Анкета", middle_name=None)
    only_url = f"{BASE}/{only_answers['id']}/questionnaire"
    assert (await client.put(only_url, json={"deportation": True})).status_code == 200
    summary = (await client.get(f"{BASE}/{only_answers['id']}/deletion")).json()
    assert (summary["passport"], summary["questionnaire"]) == (False, True)

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(f"{url}/deletion")).status_code == 404


async def test_a_card_is_deleted_with_its_whole_history(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Owner's decision 2026-11-24: a card with client history goes at once too. The
    question names everything that goes with it: its cases with their money, the paid
    consultation, the vault accounts, the stored files and the trips; the files leave the
    storage, the log keeps the facts by id."""
    tenant_id = await as_specialist(client, make_user)
    anna = await create(client, first_name="Анна")
    url = f"{BASE}/{anna['id']}"
    opened = await open_case(client, anna["id"])
    assert opened.status_code == 201, opened.text
    [case] = opened.json()
    payment = await client.post(
        f"/api/cases/{case['id']}/payments",
        json={"paid_on": "2026-05-15", "amount": "150", "currency": "BYN"},
    )
    assert payment.status_code == 201, payment.text
    held = await client.post(
        f"{url}/consultations",
        json={
            "held_on": "2026-10-10",
            "fee_amount": "50",
            "fee_currency": "BYN",
            "fee_paid": True,
            "fee_paid_on": "2026-10-10",
        },
    )
    assert held.status_code == 201, held.text
    mail = await client.post(
        "/api/dictionaries/vault-systems", json={"name": "Почта", "kind": "email"}
    )
    assert mail.status_code == 201, mail.text
    account = await client.post(
        "/api/vault/entries",
        json={"system_id": mail.json()["id"], "contact_id": anna["id"], "login": "x"},
    )
    assert account.status_code == 201, account.text
    stored = await upload(client, anna["id"])
    assert stored.status_code == 201, stored.text
    # A trip of the 90/180 calculator, a module of a later stage: written straight in.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO trips (id, tenant_id, contact_id, country_id, entered_on, left_on) "
                "SELECT gen_random_uuid(), :t, :c, id, '2026-01-01', '2026-01-05' "
                "FROM countries WHERE code = 'PL'"
            ),
            {"t": tenant_id, "c": anna["id"]},
        )
    assert (await client.get(url)).json()["can_delete"] is True

    assert (await client.get(f"{url}/deletion")).json() == NOTHING_GOES | {
        "cases": [case["number"]],
        "payments": 1,
        "payments_total": "150.00",
        "consultations": 1,
        "paid_consultations": 1,
        "vault_entries": 1,
        "documents": 1,
        "trips": 1,
    }
    assert len(storage.MEMORY) == 1

    assert (await client.delete(url)).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert storage.MEMORY == {}
    async with owner_engine.connect() as conn:
        for table in ("visa_cases", "consultations", "vault_entries", "contact_documents", "trips"):
            left = await conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
            )
            assert left == 0, table
    events = (await client.get("/api/audit")).json()["events"]
    [deleted] = [e for e in events if e["action"] == "contacts.deleted"]
    assert deleted["target_id"] == anna["id"]
    assert deleted["details"] == {"cases": 1, "files": 1}
    assert [e["target_id"] for e in events if e["action"] == "cases.deleted"] == [case["id"]]


async def test_a_storage_failure_does_not_keep_the_card(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """The rows are gone when the storage fails: what is left there is ciphertext no row
    refers to, logged by id."""
    await as_specialist(client, make_user)
    contact = await create(client)
    stored = await upload(client, contact["id"])
    assert stored.status_code == 201, stored.text

    async def failing_delete(key: str) -> None:
        raise storage.StorageFailed("ReadTimeoutError")

    monkeypatch.setattr(storage, "delete_object", failing_delete)
    assert (await client.delete(f"{BASE}/{contact['id']}")).status_code == 204
    assert (await client.get(f"{BASE}/{contact['id']}")).status_code == 404


async def test_a_card_is_not_deleted_while_an_import_runs(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """An import writing cards now could put back what the deletion takes away."""
    tenant_id = await as_specialist(client, make_user)
    contact = await create(client)
    opened = await open_case(client, contact["id"])
    assert opened.status_code == 201, opened.text
    [case] = opened.json()

    def an_import(tenant: UUID) -> list[import_jobs.ImportJob]:
        assert str(tenant) == str(tenant_id)
        return [import_jobs.ImportJob(uuid4(), tenant, "import", "clients.xlsx")]

    monkeypatch.setattr(import_jobs, "running", an_import)
    for url in (f"{BASE}/{contact['id']}", f"/api/cases/{case['id']}"):
        busy = await client.delete(url)
        assert (busy.status_code, busy.json()) == (409, {"detail": "import_running"})
        assert (await client.get(url)).status_code == 200


async def test_history_saved_while_the_card_is_deleted_goes_with_it(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """A trip saved at the moment the card is deleted (a file, an account or a case
    alike): the deletion waits for it and takes it along, so a file saved that moment
    leaves the storage too."""
    tenant_id = await as_specialist(client, make_user)
    contact = await create(client)
    trips = text("SELECT count(*) FROM trips WHERE contact_id = :c")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO trips (id, tenant_id, contact_id, country_id, entered_on, left_on) "
                "SELECT gen_random_uuid(), :t, :c, id, '2026-01-01', '2026-01-05' "
                "FROM countries WHERE code = 'PL'"
            ),
            {"t": tenant_id, "c": contact["id"]},
        )
        answer = asyncio.ensure_future(client.delete(f"{BASE}/{contact['id']}"))
        reached = await _all_waiting(conn, 1)
    assert reached, "the deletion never waited for the trip"
    response = await answer
    assert response.status_code == 204
    async with owner_engine.connect() as conn:
        assert await conn.scalar(trips, {"c": contact["id"]}) == 0


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
    assert (await client.get(f"{BASE}/{mine['id']}/deletion")).status_code == 403
    assert (await client.delete(f"{BASE}/{mine['id']}")).status_code == 403
    client.cookies.clear()

    await as_specialist(client, make_user, "bella@example.com")
    assert (await client.get(f"{BASE}/{contact['id']}/deletion")).status_code == 404
    assert (await client.delete(f"{BASE}/{contact['id']}")).status_code == 404


async def test_card_shows_no_cases_of_a_consultation_without_the_cases_module(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The card has no cases without the module (audit L3), and neither has the list of
    its consultations: «Открыт кейс №1, Польша · Подача» would tell the case's number,
    destination and stage to someone who may not open it."""
    tenant_id = await as_specialist(client, make_user)
    contact = await create(client)
    consultation = await client.post(
        f"{BASE}/{contact['id']}/consultations",
        json={"held_on": workspace_today().isoformat(), "decision": "yes"},
    )
    assert consultation.status_code == 201, consultation.text
    opened = await open_case(client, contact["id"], consultation_id=consultation.json()["id"])
    assert opened.status_code == 201, opened.text
    client.cookies.clear()
    await make_user("olga@example.com", "employee", tenant_id=tenant_id)

    async def card_with(modules: list[str]) -> dict:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET modules = :modules WHERE email = 'olga@example.com'"),
                {"modules": modules},
            )
        return (await client.get(f"{BASE}/{contact['id']}")).json()

    await give_cases(owner_engine, "olga@example.com")
    await login(client, "olga@example.com")
    without = await card_with(["clients"])
    assert without["cases"] is None
    assert [c["cases"] for c in without["consultations"]] == [[]]
    # With the module the same employee sees which case the consultation led to.
    with_cases = await card_with(["clients", "cases"])
    [[case]] = [c["cases"] for c in with_cases["consultations"]]
    assert case["id"] == opened.json()[0]["id"]


# --- A new card with its first conversation (backlog 1.15.16) ----------------------------


async def country_id(client: AsyncClient, code: str) -> str:
    countries = (await client.get("/api/reference/countries")).json()
    return next(c["id"] for c in countries if c["code"] == code)


async def audit_actions(client: AsyncClient) -> list[str]:
    return [e["action"] for e in (await client.get("/api/audit")).json()["events"]]


async def test_a_new_card_takes_its_questionnaire_consultation_and_note_at_once(
    client: AsyncClient, make_user: MakeUser
):
    """The specialist opens the card on the first conversation, and what the person told
    her goes in with it: the questionnaire, the consultation and a note, one button."""
    await as_specialist(client, make_user)
    poland = await country_id(client, "PL")
    anna = await create(
        client,
        passport={"passport_number": "MP1234567"},
        questionnaire={
            "visas": [{"kind": "schengen", "country": "PL", "valid_to": "2025-05-01"}],
            "refusals": [{"country": "FR", "year": 2023}],
            "deportation": False,
            "note": "Работает в IT",
        },
        consultation={
            "held_on": "2026-11-28",
            "destination_ids": [poland],
            "applicants": 2,
            "notes": "Хотят в Польшу летом",
        },
        note="Пришла из Telegram, муж тоже поедет",
    )
    card = (await client.get(f"{BASE}/{anna['id']}")).json()
    q = card["questionnaire"]
    assert [v["country"] for v in q["visas"]] == ["PL"]
    assert [r["country"] for r in q["refusals"]] == ["FR"]
    assert (q["note"], q["passport_number"]) == ("Работает в IT", "MP1234567")
    [consultation] = card["consultations"]
    assert consultation["destination_ids"] == [poland]
    assert (consultation["applicants"], consultation["notes"]) == (2, "Хотят в Польшу летом")
    assert [n["body"] for n in card["notes"]] == ["Пришла из Telegram, муж тоже поедет"]


async def test_empty_blocks_create_nothing(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(
        client,
        questionnaire={"visas": [], "refusals": [], "deportation": False, "note": "  "},
        consultation=None,
        note="   ",
    )
    card = (await client.get(f"{BASE}/{anna['id']}")).json()
    assert (card["consultations"], card["notes"]) == ([], [])
    assert card["questionnaire"]["visas"] == []
    actions = await audit_actions(client)
    assert "contacts.questionnaire_updated" not in actions
    assert "contacts.note_added" not in actions


async def test_the_consultation_takes_the_cards_channel(client: AsyncClient, make_user: MakeUser):
    """The channel is typed once, in «Контакт»; the card's consultation may change it."""
    await as_specialist(client, make_user)
    channel = (await client.post("/api/dictionaries/channels", json={"name": "Instagram"})).json()
    anna = await create(
        client, lead_channel_id=channel["id"], consultation={"held_on": "2026-11-28"}
    )
    [consultation] = (await client.get(f"{BASE}/{anna['id']}")).json()["consultations"]
    assert consultation["lead_channel_id"] == channel["id"]


async def test_a_wrong_block_saves_nothing(client: AsyncClient, make_user: MakeUser):
    """A mistake in any block keeps the whole form unsaved: no card, no log lines."""
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    foreign = await client.post("/api/dictionaries/channels", json={"name": "Чужой"})
    foreign_channel = foreign.json()["id"]
    client.cookies.clear()
    await as_specialist(client, make_user)
    await client.patch("/api/dictionaries/money", json={"payment_currencies": ["USD"]})

    before = await audit_actions(client)
    held = {"held_on": "2026-11-28"}
    wrong = [
        ({"consultation": {**held, "applicants": 0}}, 422, "validation"),
        ({"consultation": {**held, "fee_amount": "50"}}, 422, "validation"),
        (
            {"consultation": {**held, "fee_amount": "50", "fee_currency": "PLN"}},
            422,
            "currency_not_accepted",
        ),
        ({"consultation": {**held, "destination_ids": [str(uuid4())]}}, 404, "not_found"),
        ({"consultation": {**held, "lead_channel_id": foreign_channel}}, 404, "not_found"),
        ({"questionnaire": {"refusals": [{"country": "France"}]}}, 422, "validation"),
        ({"note": "x" * 5001}, 422, "validation"),
    ]
    for block, status_code, detail in wrong:
        body = {"first_name": "Анна", "phone": "+375290001234", "note": "заметка", **block}
        response = await client.post(BASE, json=body)
        assert response.status_code == status_code, (block, response.text)
        if detail != "validation":
            assert response.json()["detail"] == detail
    assert await search(client) == []
    assert await audit_actions(client) == before


async def test_a_shared_nick_saved_anyway_keeps_every_block(
    client: AsyncClient, make_user: MakeUser
):
    await as_specialist(client, make_user)
    await create(client, telegram="anna_k_visa")
    body = {
        "first_name": "Мария",
        "telegram": "anna_k_visa",
        "questionnaire": {"deportation": True},
        "consultation": {"held_on": "2026-11-28", "notes": "сестра Анны"},
        "note": "общий Telegram с сестрой",
    }
    warned = await client.post(BASE, json=body)
    assert warned.status_code == 409
    assert len(await search(client)) == 1  # «Открыть карточку» creates nothing

    saved = await client.post(BASE, params={"allow_shared_telegram": True}, json=body)
    assert saved.status_code == 201, saved.text
    card = (await client.get(f"{BASE}/{saved.json()['id']}")).json()
    assert card["questionnaire"]["deportation"] is True
    assert [c["notes"] for c in card["consultations"]] == ["сестра Анны"]
    assert [n["body"] for n in card["notes"]] == ["общий Telegram с сестрой"]


async def test_a_new_card_logs_what_the_steps_would(client: AsyncClient, make_user: MakeUser):
    await as_specialist(client, make_user)
    anna = await create(
        client,
        phone="+375290001234",
        questionnaire={"refusals": [{"country": "FR"}]},
        consultation={"held_on": "2026-11-28", "notes": "секрет консультации"},
        note="секрет заметки",
    )
    events = (await client.get("/api/audit")).json()["events"]
    mine = {e["action"]: e for e in events if e["action"] != "auth.login"}
    assert set(mine) == {
        "contacts.created",
        "contacts.questionnaire_updated",
        "consultations.created",
        "contacts.note_added",
    }
    assert mine["contacts.created"]["target_id"] == anna["id"]
    assert mine["contacts.questionnaire_updated"]["target_id"] == anna["id"]
    assert mine["consultations.created"]["details"] == {"contact": anna["id"]}
    assert mine["contacts.note_added"]["target_id"] == anna["id"]
    assert set(mine["contacts.note_added"]["details"]) == {"note"}
    for event in events:
        for secret in ("Ковалёва", "375290001234", "секрет", "FR"):
            assert secret not in str(event["details"])


async def test_an_employee_creates_a_card_with_its_consultation(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """An employee's new card goes in whole and is theirs: the other employee and another
    specialist do not see it. Creating spends nothing of the daily limit of cards."""
    tenant_id = await as_specialist(client, make_user)
    client.cookies.clear()
    for email in ("olga@example.com", "ivan@example.com"):
        await make_user(email, "employee", tenant_id=tenant_id)
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE users SET modules = '{clients}' WHERE role = 'employee'"))
    await login(client, "olga@example.com")
    created = await client.post(
        BASE,
        json={
            "first_name": "Анна",
            "questionnaire": {"deportation": True},
            "consultation": {"held_on": "2026-11-28", "notes": "вводные"},
            "note": "от сотрудника",
        },
    )
    assert created.status_code == 201, created.text
    contact_id = created.json()["id"]
    async with owner_engine.connect() as conn:
        viewed = await conn.scalar(
            text("SELECT count(*) FROM audit_events WHERE action = 'contacts.viewed'")
        )
    assert viewed == 0
    card = (await client.get(f"{BASE}/{contact_id}")).json()
    assert card["questionnaire"]["deportation"] is True
    assert [c["notes"] for c in card["consultations"]] == ["вводные"]
    assert [n["body"] for n in card["notes"]] == ["от сотрудника"]
    async with owner_engine.connect() as conn:
        viewed = await conn.scalar(
            text("SELECT count(*) FROM audit_events WHERE action = 'contacts.viewed'")
        )
    assert viewed == 1  # the first open of the card is what counts

    client.cookies.clear()
    await login(client, "ivan@example.com")
    assert (await client.get(f"{BASE}/{contact_id}")).status_code == 404
    client.cookies.clear()
    await as_specialist(client, make_user, "boris2@example.com")
    assert (await client.get(f"{BASE}/{contact_id}")).status_code == 404

"""Карточки до загрузки (backlog 1.22, docs/data-format.md → «Проверка и загрузка»): the
specialist uploads her file once and it waits on the server — nothing goes into the CRM. She
sees a few sample cards, has the whole file checked, looks through every card — what is new,
what changes — and only then confirms the import. A password is never shown, another
workspace sees nothing, and «Отменить» forgets the file."""

import base64
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import import_uploads
from tests.helpers import MakeUser, finished_import, import_report, login, uploaded
from tests.test_data_import import BOOK, PAYMENTS, body, count, mail_system

API = "/api/imports"
TABLES = ("contacts", "consultations", "visa_cases", "case_payments", "vault_entries")
# Seven people with a case and a payment each: more than a sample.
MANY = {
    "Клиенты": [["Код клиента", "Фамилия", "Имя", "Телефон"]]
    + [[f"K{i}", f"Клиент{i}", "Анна", f"+37529111000{i}"] for i in range(1, 8)],
    "Кейсы": [["Код кейса", "Код клиента", "Страна визы", "Стоимость"]]
    + [[f"C{i}", f"K{i}", "Польша", 100 * i] for i in range(1, 8)],
    "Платежи": [["Код кейса", "Дата", "Сумма"]]
    + [[f"C{i}", date(2025, 3, i), 50] for i in range(1, 8)],
}


async def ok(response) -> dict | list | None:
    assert response.status_code in (200, 201, 202, 204), response.text
    return response.json() if response.status_code != 204 else None


async def sample(client: AsyncClient, upload_id: str, language: str = "ru") -> list[dict]:
    started = await client.post(f"{API}/uploads/{upload_id}/sample")
    job = await finished_import(client, started)
    assert (job["kind"], job["status"]) == ("sample", "done"), job
    return await ok(  # type: ignore[return-value]
        await client.get(f"{API}/uploads/{upload_id}/sample", params={"language": language})
    )


async def check(client: AsyncClient, upload_id: str, choices: dict | None = None) -> dict:
    started = await client.post(f"{API}/uploads/{upload_id}/check", json={"choices": choices or {}})
    job = await finished_import(client, started)
    assert job["status"] == "done", job
    return job["report"]


async def card(client: AsyncClient, upload_id: str, n: int, language: str = "ru") -> dict:
    return await ok(  # type: ignore[return-value]
        await client.get(f"{API}/uploads/{upload_id}/cards/{n}", params={"language": language})
    )


def section(sections: list[dict], title: str) -> list[dict]:
    """The records of a card's section by its title."""
    return next(s["records"] for s in sections if s["title"] == title)


def values(record: dict) -> dict:
    return {f["label"]: f["value"] for f in record["fields"]}


async def written(engine: AsyncEngine, tenant) -> dict[str, int]:
    return {table: await count(engine, table, tenant) for table in TABLES}


NOTHING = dict.fromkeys(TABLES, 0)


async def test_the_file_waits_on_the_server_and_writes_nothing(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    file = body(BOOK)

    upload = await uploaded(client, file)

    assert upload["file_name"] == "VisaCRM-импорт.xlsx"
    assert upload["size"] == len(base64.b64decode(file["content"]))
    assert upload["clients"] == 3
    assert {"title": "Клиенты", "rows": 3} in upload["sheets"]
    assert (upload["sampled"], upload["checked"], upload["report"]) == (False, False, None)
    # The page opened again finds the file where it was.
    assert (await ok(await client.get(f"{API}/uploads/current")))["id"] == upload["id"]  # type: ignore[index]
    assert await written(owner_engine, tenant) == NOTHING


async def test_the_sample_is_the_first_clients_cards_with_all_of_theirs(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    upload = await uploaded(client, body(MANY))

    cards = await sample(client, upload["id"])

    assert [c["name"] for c in cards] == [f"Клиент{i} Анна" for i in range(1, 6)]
    first = cards[0]
    assert first["status"] == "new" and first["phone"] == "+375291110001"
    assert [s["title"] for s in first["sections"]] == ["Клиенты", "Кейсы"]
    [client_record] = section(first["sections"], "Клиенты")
    assert client_record["change"] == "new"
    assert values(client_record) == {
        "Фамилия": "Клиент1",
        "Имя": "Анна",
        "Телефон": "+375291110001",
    }
    [case] = section(first["sections"], "Кейсы")
    assert case["change"] == "new"
    assert values(case)["Страна визы"] == "Польша" and values(case)["Стоимость"] == "100.00"
    [payment] = section(case["sections"], "Платежи")
    assert values(payment) == {
        "Дата": "2025-03-01",
        "Сумма": "50.00",
        "Валюта": "BYN",
    }

    english = await sample(client, upload["id"], "en")
    assert [s["title"] for s in english[0]["sections"]] == ["Clients", "Cases"]
    assert "Visa country" in values(section(english[0]["sections"], "Cases")[0])
    current = await ok(await client.get(f"{API}/uploads/current"))
    assert (current["sampled"], current["checked"]) == (True, False)  # type: ignore[index]
    assert await written(owner_engine, tenant) == NOTHING


async def test_the_check_gives_every_card_and_the_import_makes_them(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    upload = await uploaded(client, body(BOOK))

    report = await check(client, upload["id"])

    assert report["imported"] > 0 and report["id"] is None
    cards = await ok(await client.get(f"{API}/uploads/{upload['id']}/cards"))
    assert [(c["n"], c["name"], c["status"], c["cases"], c["consultations"]) for c in cards] == [  # type: ignore[union-attr]
        (0, "Петрова Анна Сергеевна", "new", 1, 1),
        (1, "Петров Игорь", "new", 1, 0),
        (2, "Сидоренко Ольга", "new", 1, 0),
        # The specialist's own Booking account: a card of no client, last.
        (3, None, "new", 0, 0),
    ]

    anna = await card(client, upload["id"], 0)
    assert [s["title"] for s in anna["sections"]] == ["Клиенты", "Заметки", "Консультации", "Кейсы"]
    [note] = section(anna["sections"], "Заметки")
    assert values(note)["Текст"] == "Звонить после 18:00"
    [consultation] = section(anna["sections"], "Консультации")
    # The case opened from it: «думает» became «да», as when a case is opened by hand.
    assert values(consultation)["Решение"] == "да"
    assert values(consultation)["Страны"] == "Польша, Латвия"
    [case] = section(anna["sections"], "Кейсы")
    assert values(case)["Семья"] == "Петров Игорь"
    assert values(case)["Результат"] == "виза" and values(case)["Регистратор"] == "Иван"
    # The money as the case's finances count it, as «Оплачено» and «Остаток» of an export.
    assert values(case)["Стоимость"] == "800.00"
    assert {"Оплачено", "Остаток"} <= set(values(case))
    assert [s["title"] for s in case["sections"]] == [
        "Платежи",
        "Расходы",
        "Очередь",
        "Брони",
        "Учётные записи",
    ]
    assert len(section(case["sections"], "Платежи")) == 2
    [account] = section(case["sections"], "Учётные записи")
    assert values(account)["Логин"] == "anna.p@example.com" and values(account)["Пароль"] is True
    own = await card(client, upload["id"], 3)
    assert own["name"] is None
    [mine] = section(own["sections"], "Учётные записи")
    assert values(mine)["Логин"] == "booking.mine@example.com"
    # A password is never sent back: only that there is one.
    for n in range(4):
        shown = (await client.get(f"{API}/uploads/{upload['id']}/cards/{n}")).text
        assert "Pa55word" not in shown and "BkPass1" not in shown
    missing = await client.get(f"{API}/uploads/{upload['id']}/cards/4")
    assert missing.status_code == 404 and missing.json()["detail"] == "not_found"
    assert await written(owner_engine, tenant) == NOTHING

    started = await client.post(f"{API}/uploads/{upload['id']}/import", json={"choices": {}})
    done = await finished_import(client, started)

    assert (done["kind"], done["status"]) == ("import", "done"), done
    assert done["report"]["created"]["contacts"] == 3
    assert (await written(owner_engine, tenant))["contacts"] == 3
    # A loaded file is forgotten with its cards.
    assert await ok(await client.get(f"{API}/uploads/current")) is None
    gone = await client.get(f"{API}/uploads/{upload['id']}/cards")
    assert gone.status_code == 404 and gone.json()["detail"] == "import_upload_not_found"


async def test_a_file_loaded_before_marks_only_what_changes(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await import_report(client, body(BOOK))
    paid_again = PAYMENTS + [
        ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 6, 1), 100, "BYN", "нет"]
    ]
    upload = await uploaded(client, body(BOOK | {"Платежи": paid_again}))

    await check(client, upload["id"])

    cards = await ok(await client.get(f"{API}/uploads/{upload['id']}/cards"))
    assert [(c["name"], c["status"]) for c in cards] == [  # type: ignore[union-attr]
        ("Петрова Анна Сергеевна", "updated"),
        ("Петров Игорь", "same"),
        ("Сидоренко Ольга", "same"),
        (None, "same"),
    ]
    anna = await card(client, upload["id"], 0)
    assert section(anna["sections"], "Клиенты")[0]["change"] is None
    [case] = section(anna["sections"], "Кейсы")
    assert case["change"] is None
    payments = section(case["sections"], "Платежи")
    assert [(values(p)["Дата"], p["change"]) for p in payments] == [
        ("2025-01-15", None),
        ("2025-05-20", None),
        ("2025-06-01", "new"),
    ]


async def test_the_import_waits_for_the_check_and_cancel_forgets_the_file(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    upload = await uploaded(client, body(MANY))
    path = f"{API}/uploads/{upload['id']}"

    early = await client.post(f"{path}/import", json={"choices": {}})
    assert early.status_code == 409 and early.json()["detail"] == "import_not_checked"
    not_yet = await client.get(f"{path}/cards")
    assert not_yet.status_code == 404 and not_yet.json()["detail"] == "import_not_checked"

    assert await ok(await client.delete(path)) is None
    assert await ok(await client.get(f"{API}/uploads/current")) is None
    gone = await client.post(f"{path}/check", json={"choices": {}})
    assert gone.status_code == 404 and gone.json()["detail"] == "import_upload_not_found"


async def test_a_new_file_takes_the_place_of_the_waiting_one(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    older = await uploaded(client, body(MANY))

    newer = await uploaded(client, body(BOOK))

    assert (await ok(await client.get(f"{API}/uploads/current")))["id"] == newer["id"]  # type: ignore[index]
    gone = await client.post(f"{API}/uploads/{older['id']}/sample")
    assert gone.status_code == 404 and gone.json()["detail"] == "import_upload_not_found"


async def test_another_workspace_sees_no_file_and_no_cards(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await make_user("other@example.com")
    await login(client, "spec@example.com")
    upload = await uploaded(client, body(MANY))
    await sample(client, upload["id"])
    await check(client, upload["id"])
    path = f"{API}/uploads/{upload['id']}"

    client.cookies.clear()
    await login(client, "other@example.com")
    assert await ok(await client.get(f"{API}/uploads/current")) is None
    for response in (
        await client.get(f"{path}/sample"),
        await client.get(f"{path}/cards"),
        await client.get(f"{path}/cards/0"),
        await client.post(f"{path}/sample"),
        await client.post(f"{path}/check", json={"choices": {}}),
        await client.post(f"{path}/import", json={"choices": {}}),
        await client.delete(path),
    ):
        assert response.status_code == 404 and response.json()["detail"] == (
            "import_upload_not_found"
        )

    client.cookies.clear()
    await login(client, "spec@example.com")
    assert (await ok(await client.get(f"{API}/uploads/current")))["id"] == upload["id"]  # type: ignore[index]


async def test_a_file_is_forgotten_a_day_after_its_last_use(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    now = [1000.0]
    monkeypatch.setattr(import_uploads, "_now", lambda: now[0])
    upload = await uploaded(client, body(MANY))

    now[0] += import_uploads.KEEP - 1
    assert (await ok(await client.get(f"{API}/uploads/current")))["id"] == upload["id"]  # type: ignore[index]
    now[0] += import_uploads.KEEP - 1
    assert (await ok(await client.get(f"{API}/uploads/current")))["id"] == upload["id"]  # type: ignore[index]
    now[0] += import_uploads.KEEP + 1
    assert await ok(await client.get(f"{API}/uploads/current")) is None

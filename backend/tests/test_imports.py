"""Импорт таблиц (backlog 1.14): the specialist's spreadsheets become contacts,
consultations, cases, queue entries, windowed applications, bookings and vault accounts;
one person from several tables is one contact, and a repeated import updates instead of
adding duplicates."""

import base64
import io
import uuid
from datetime import datetime

import openpyxl
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login

API = "/api/imports"


async def ok(response) -> dict:
    assert response.status_code in (200, 201, 204), response.text
    return response.json() if response.status_code != 204 else {}


def csv_file(rows: list[list[str]]) -> str:
    return base64.b64encode("\n".join(";".join(r) for r in rows).encode()).decode()


def xlsx_file(sheets: dict[str, list[list]]) -> str:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


def body(kind: str, name: str, content: str, preview: dict | None = None, **extra) -> dict:
    """A request; with a preview, its suggested mapping and options."""
    data = {"kind": kind, "file_name": name, "content": content}
    if preview is not None:
        data["columns"] = {str(c["index"]): c["field"] for c in preview["columns"] if c["field"]}
        data["options"] = {k: v for k, v in preview["options"].items() if v is not None}
    return data | extra


async def count(engine: AsyncEngine, table: str, tenant_id: uuid.UUID) -> int:
    async with engine.connect() as conn:
        return await conn.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
        )


async def one(engine: AsyncEngine, sql: str, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).mappings().one()


CONSULTATIONS = [
    ["Консультации 2026"],  # a title above the header
    ["ФИО", "Контакты", "Дата консультации", "Решение", "Страна", "Канал", "Оплата", "Кейс"],
    [
        "Иванова Анна",
        "+375 29 111-11-11 @anna_iv",
        "12.05.2026",
        "да",
        "Польша",
        "Инста",
        "50",
        "Хочет в Польшу летом",
    ],
    ["Петров Пётр", "80291112233", "20.05.2026", "думает", "Италия, Испания", "Instagram", "", ""],
    ["", "", "", "", "", "", "", ""],
    ["Сидорова Ольга", "@olga_s", "32.05.2026", "нет", "", "", "", ""],
    ["", "+375 29 999-99-99", "10.05.2026", "да", "", "", "", ""],
]


async def test_consultations_cases_and_repeated_import(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    insta = await ok(await client.post("/api/dictionaries/channels", json={"name": "Instagram"}))

    content = csv_file(CONSULTATIONS)
    preview = await ok(
        await client.post(f"{API}/preview", json=body("consultations", "c.csv", content))
    )
    assert preview["header_row"] == 2 and preview["rows"] == 4
    assert [c["field"] for c in preview["columns"]] == [
        "full_name",
        "contacts",
        "held_on",
        "decision",
        "destinations",
        "channel",
        "fee_amount",
        "consultation_notes",
    ]
    assert preview["columns"][0]["samples"] == ["Иванова Анна", "Петров Пётр", "Сидорова Ольга"]

    # The check writes nothing and lists the channel it does not know.
    request = body("consultations", "c.csv", content, preview)
    checked = await ok(await client.post(f"{API}/check", json=request))
    assert checked["id"] is None
    assert checked["imported"] == 2
    assert {(s["row"], s["reason"]) for s in checked["skipped"]} == {(6, "no_date"), (7, "no_name")}
    assert checked["unknown"]["channel"] == [{"key": "инста", "value": "Инста", "rows": 1}]
    assert checked["created"] == {"contacts": 2, "consultations": 2, "channel": 1}
    assert await count(owner_engine, "contacts", tenant_id) == 0
    assert await count(owner_engine, "lead_channels", tenant_id) == 1

    # «Инста» is the Instagram channel the specialist already has.
    request["values"] = {"channel": {"инста": insta["id"]}}
    report = await ok(await client.post(API, json=request))
    assert report["id"] and report["created"] == {"contacts": 2, "consultations": 2}
    anna = await one(
        owner_engine,
        "SELECT id, phone, telegram, lead_channel_id FROM contacts "
        "WHERE full_name = 'Иванова Анна'",
    )
    assert (anna["phone"], anna["telegram"]) == ("+375291111111", "anna_iv")
    assert str(anna["lead_channel_id"]) == insta["id"]
    consultation = await one(
        owner_engine,
        "SELECT held_on, decision, fee_amount, fee_currency, fee_paid, fee_paid_on, notes, "
        "cardinality(destination_ids) AS countries FROM consultations WHERE contact_id = :c",
        c=anna["id"],
    )
    assert consultation["decision"] == "yes" and consultation["fee_paid"]
    assert (str(consultation["fee_amount"]), consultation["fee_currency"]) == ("50.00", "BYN")
    assert consultation["fee_paid_on"] == consultation["held_on"]
    assert consultation["notes"] == "Хочет в Польшу летом"
    petr = await one(owner_engine, "SELECT id, phone FROM contacts WHERE full_name = 'Петров Пётр'")
    assert petr["phone"] == "+375291112233"
    assert (
        await one(
            owner_engine,
            "SELECT cardinality(destination_ids) AS n FROM consultations WHERE contact_id = :c",
            c=petr["id"],
        )
    )["n"] == 2

    # The same file again: the mapping is remembered, nothing is added or changed.
    preview = await ok(
        await client.post(f"{API}/preview", json=body("consultations", "c.csv", content))
    )
    assert preview["values"] == {"channel": {"инста": insta["id"]}}
    again = await ok(
        await client.post(
            API,
            json=body("consultations", "c.csv", content, preview) | {"values": preview["values"]},
        )
    )
    assert again["imported"] == 2 and again["created"] == {} and again["updated"] == {}
    assert await count(owner_engine, "consultations", tenant_id) == 2

    # «Клиенты в работе»: the same people by phone and by name, cases with money,
    # a queue date, a result and the mailbox to the vault.
    systems = await ok(await client.get("/api/dictionaries/vault-systems"))
    mail = next((s for s in systems if s["kind"] == "email"), None) or await ok(
        await client.post(
            "/api/dictionaries/vault-systems", json={"name": "Почта", "kind": "email"}
        )
    )
    work = [
        [
            "ФИО",
            "Телефон",
            "Страна",
            "Цель",
            "Дата постановки",
            "Подача",
            "Результат",
            "Стоимость",
            "Оплачено",
            "Почта",
            "Пароль",
        ],
        [
            "Анна Иванова",
            "+375291111111",
            "Польша",
            "Туризм",
            datetime(2026, 5, 15),
            datetime(2026, 6, 20),
            "Мульти на 2 года",
            400,
            "400 р",
            "anna.visa@mail.ru",
            "S3cret!",
        ],
        ["Пётр Петров", None, "Италия", None, "01.06.2026", None, None, 300, "100 EUR", None, None],
    ]
    content = xlsx_file({"В работе": work})
    preview = await ok(await client.post(f"{API}/preview", json=body("cases", "w.xlsx", content)))
    assert preview["sheets"] == ["В работе"]
    fields = [c["field"] for c in preview["columns"]]
    assert fields == [
        "full_name",
        "phone",
        "destination",
        "purpose",
        "queued_on",
        "submission_on",
        "outcome",
        "price",
        "paid",
        "email_login",
        "email_password",
    ]
    assert preview["columns"][10]["samples"] == ["••••••"]
    assert preview["options"]["email_system_id"] == mail["id"]
    report = await ok(await client.post(API, json=body("cases", "w.xlsx", content, preview)))
    assert report["created"] == {
        "cases": 2,
        "purpose": 1,
        "queue_entries": 2,
        "finances": 2,
        "payments": 1,
        "vault_entries": 1,
        "passwords": 1,
    }
    assert report["updated"] == {}
    assert report["warnings"] == [{"row": 3, "column": "Оплачено", "reason": "foreign_currency"}]
    assert await count(owner_engine, "contacts", tenant_id) == 2

    case = await one(
        owner_engine,
        "SELECT id, number, stage, outcome, visa_entries, submission_on, application_email, "
        "consultation_id, created_at FROM visa_cases WHERE contact_id = :c",
        c=anna["id"],
    )
    assert (case["stage"], case["outcome"], case["visa_entries"]) == ("result", "visa", "multiple")
    assert case["application_email"] == "anna.visa@mail.ru"
    # Started from her consultation, in the month of that consultation.
    assert case["consultation_id"] is not None
    assert case["created_at"].date().isoformat() == "2026-05-12"
    finance = await one(
        owner_engine, "SELECT price FROM case_finances WHERE case_id = :c", c=case["id"]
    )
    assert str(finance["price"]) == "400.00"
    payment = await one(
        owner_engine, "SELECT amount, paid_on FROM case_payments WHERE case_id = :c", c=case["id"]
    )
    assert str(payment["amount"]) == "400.00"
    vault = await ok(await client.get(f"/api/contacts/{anna['id']}/vault"))
    [entry] = vault if isinstance(vault, list) else vault["entries"]
    revealed = await ok(await client.post(f"/api/vault/entries/{entry['id']}/reveal"))
    assert revealed["password"] == "S3cret!"

    # The table changed: Petr got a refusal. The rows update, nothing is duplicated.
    work[2][6] = "отказ"
    content = xlsx_file({"В работе": work})
    preview = await ok(await client.post(f"{API}/preview", json=body("cases", "w.xlsx", content)))
    again = await ok(await client.post(API, json=body("cases", "w.xlsx", content, preview)))
    assert again["created"] == {}
    assert again["updated"] == {"cases": 1}
    assert await count(owner_engine, "visa_cases", tenant_id) == 2
    assert await count(owner_engine, "vault_entries", tenant_id) == 1
    assert await count(owner_engine, "case_payments", tenant_id) == 1

    # Petr «thinking» in the consultations table came to work: his «yes» stays.
    consultations = csv_file(CONSULTATIONS)
    preview = await ok(
        await client.post(f"{API}/preview", json=body("consultations", "c.csv", consultations))
    )
    again = await ok(
        await client.post(
            API,
            json=body("consultations", "c.csv", consultations, preview)
            | {"values": preview["values"]},
        )
    )
    assert again["created"] == {} and again["updated"] == {}
    decision = await one(
        owner_engine, "SELECT decision FROM consultations WHERE contact_id = :c", c=petr["id"]
    )
    assert decision["decision"] == "yes"

    # The Polish queue: Anna's case is found, Oleg is new; the country from the file name.
    queue = csv_file(
        [
            ["ФИО", "ID", "Дата постановки", "Письмо"],
            ["Иванова Анна", "PL123", "15.05.2026", "01.06.2026"],
            ["Новиков Олег", "PL124", "20.05.2026", ""],
        ]
    )
    preview = await ok(await client.post(f"{API}/preview", json=body("queue", "Польша.csv", queue)))
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    assert preview["options"]["country_id"] == countries["PL"]
    report = await ok(await client.post(API, json=body("queue", "Польша.csv", queue, preview)))
    assert report["created"] == {"contacts": 1, "cases": 1, "queue_entries": 1}
    assert report["updated"] == {"cases": 1, "queue_entries": 1}
    entry = await one(
        owner_engine,
        "SELECT q.letter_on, c.vac_number FROM waiting_list_entries q "
        "JOIN visa_cases c ON c.id = q.case_id WHERE c.contact_id = :c",
        c=anna["id"],
    )
    assert entry["vac_number"] == "PL123" and entry["letter_on"].isoformat() == "2026-06-01"

    # France: windowed applications; the fourth attempt after three repeats.
    attempts = csv_file(
        [
            ["ФИО", "Номер анкеты", "Дата заявки", "Повторы"],
            ["Новиков Олег", "FR-777", "01.06.2026", "3"],
        ]
    )
    preview = await ok(
        await client.post(f"{API}/preview", json=body("attempts", "Франция.csv", attempts))
    )
    report = await ok(
        await client.post(API, json=body("attempts", "Франция.csv", attempts, preview))
    )
    assert report["created"] == {"cases": 1, "attempts": 1}
    attempt = await one(
        owner_engine,
        "SELECT a.number, a.sent_on, a.deadline_on FROM application_attempts a "
        "JOIN visa_cases c ON c.id = a.case_id WHERE c.vac_number = 'FR-777'",
    )
    assert attempt["number"] == 4
    assert (attempt["deadline_on"] - attempt["sent_on"]).days == 60

    # Bookings go to the person's case; a person without a case is skipped.
    bookings = csv_file(
        [
            ["ФИО", "Отель", "Отмена до", "Номер брони"],
            ["Иванова Анна", "Hotel Warszawa", "10.06.2026", "BK-1"],
            ["Незнакомый Человек", "Hotel", "10.06.2026", "BK-2"],
        ]
    )
    preview = await ok(
        await client.post(f"{API}/preview", json=body("bookings", "b.csv", bookings))
    )
    report = await ok(await client.post(API, json=body("bookings", "b.csv", bookings, preview)))
    assert report["created"] == {"bookings": 1}
    assert report["skipped"] == [{"row": 3, "reason": "no_case", "column": None}]

    # The history of runs and the audit log: counts only.
    runs = await ok(await client.get(API))
    assert [r["kind"] for r in runs] == [
        "bookings",
        "attempts",
        "queue",
        "consultations",
        "cases",
        "cases",
        "consultations",
        "consultations",
    ]
    stored = await ok(await client.get(f"{API}/{runs[0]['id']}"))
    assert stored["skipped_count"] == 1
    event = await one(
        owner_engine,
        "SELECT details FROM audit_events WHERE action = 'imports.completed' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert event["details"]["kind"] == "bookings"
    assert "Иванова" not in str(event["details"])


async def test_who_may_import_and_bad_files(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await make_user("helper@example.com", "employee", tenant_id=tenant_id)
    await make_user("boris@example.com")
    content = csv_file([["ФИО", "Дата консультации"], ["Иванова Анна", "12.05.2026"]])

    # An employee does not import, even with the client index.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET modules = '{clients,cases}' WHERE email = 'helper@example.com'")
        )
    await login(client, "helper@example.com")
    response = await client.post(f"{API}/preview", json=body("consultations", "c.csv", content))
    assert response.status_code == 403

    await login(client, "anna@example.com")
    preview = await ok(
        await client.post(f"{API}/preview", json=body("consultations", "c.csv", content))
    )
    request = body("consultations", "c.csv", content, preview)
    run = await ok(await client.post(API, json=request))

    # A required field must be mapped; a column cannot be two fields.
    assert (await client.post(f"{API}/check", json=request | {"columns": {"1": "held_on"}})).json()[
        "detail"
    ] == "required_field"
    response = await client.post(
        f"{API}/check", json=request | {"columns": {"0": "full_name", "1": "full_name"}}
    )
    assert response.json()["detail"] == "duplicate_field"
    # A queue needs its country.
    queue = await client.post(
        f"{API}/check",
        json=body("queue", "q.csv", csv_file([["ФИО", "Дата постановки"], ["А Б", "01.06.2026"]]))
        | {"columns": {"0": "full_name", "1": "queued_on"}},
    )
    assert queue.json()["detail"] == "country_required"

    # Files that are not spreadsheets.
    for bad, code in (
        ("", "file_empty"),
        (base64.b64encode(b"\xd0\xcf\x11\xe0old excel").decode(), "xls_not_supported"),
        (base64.b64encode(b"PK\x03\x04broken zip").decode(), "file_unreadable"),
        (base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode(), "file_too_large"),
    ):
        response = await client.post(f"{API}/preview", json=body("consultations", "f", bad))
        assert response.json()["detail"] == code, code

    # Without the vault module the password fields are not offered.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET modules = array_remove(modules, 'vault') WHERE id = :t"),
            {"t": tenant_id},
        )
    work = csv_file([["ФИО", "Страна", "Пароль"], ["Иванова Анна", "Польша", "x"]])
    preview = await ok(await client.post(f"{API}/preview", json=body("cases", "w.csv", work)))
    assert "email_password" not in {f["key"] for f in preview["fields"]}
    response = await client.post(
        f"{API}/check",
        json=body("cases", "w.csv", work)
        | {"columns": {"0": "full_name", "1": "destination", "2": "email_password"}},
    )
    assert response.json()["detail"] == "invalid_column"

    # Another workspace sees neither the runs nor the people.
    await login(client, "boris@example.com")
    assert await ok(await client.get(API)) == []
    assert (await client.get(f"{API}/{run['id']}")).status_code == 404
    preview = await ok(
        await client.post(f"{API}/preview", json=body("consultations", "c.csv", content))
    )
    assert preview["values"] == {}
    report = await ok(await client.post(API, json=body("consultations", "c.csv", content, preview)))
    assert report["created"] == {"contacts": 1, "consultations": 1}

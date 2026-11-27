"""Import of the VisaCRM data format (backlog 1.21, docs/data-format.md): one workbook with
every sheet at once, linked by codes, read strictly. A repeated or re-made file finds the
records it made; a row of a CRM export is a snapshot of its record and is not loaded over
a later change; codes of another workspace find nothing; the report never holds a value
of the file."""

import base64
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import openpyxl
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import rates
from tests.support.helpers import WALL_CLOCK, MakeUser, import_report, login, workspace_today

API = "/api/imports"


async def ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()


def xlsx(sheets: dict[str, list[list]]) -> str:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


def body(sheets: dict[str, list[list]], **extra) -> dict:
    return {"file_name": "VisaCRM-импорт.xlsx", "content": xlsx(sheets)} | extra


async def check(client: AsyncClient, sheets: dict[str, list[list]], **extra) -> dict:
    return await import_report(client, body(sheets, **extra), save=False)


async def run(client: AsyncClient, sheets: dict[str, list[list]], **extra) -> dict:
    return await import_report(client, body(sheets, **extra))


async def count(engine: AsyncEngine, table: str, tenant_id: uuid.UUID) -> int:
    async with engine.connect() as conn:
        return await conn.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
        )


async def rows(engine: AsyncEngine, sql: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings()]


async def mail_system(client: AsyncClient) -> None:
    for name, kind in (("Почта", "email"), ("Booking.com", "booking")):
        await ok(
            await client.post("/api/dictionaries/vault-systems", json={"name": name, "kind": kind})
        )


CLIENTS = [
    [
        "Код клиента",
        "Фамилия",
        "Имя",
        "Отчество",
        "Телефон",
        "Telegram",
        "Дата рождения",
        "Канал прихода",
    ],
    [
        "Подача25-2",
        "Петрова",
        "Анна",
        "Сергеевна",
        "+375291112233",
        "@anna_p",
        date(1988, 3, 12),
        "Сарафан",
    ],
    ["Подача25-3", "Петров", "Игорь", None, "+375297778899", None, None, "Сарафан"],
    ["Франция-2", "Сидоренко", "Ольга", None, None, None, None, None],
]
NOTES = [
    ["Код клиента", "Клиент", "Текст"],
    ["Подача25-2", "Петрова Анна Сергеевна", "Звонить после 18:00"],
]
CONSULTATIONS = [
    [
        "Код консультации",
        "Код клиента",
        "Клиент",
        "Дата",
        "Заявителей",
        "Страны",
        "Решение",
        "Стоимость",
        "Валюта",
        "Оплачено",
    ],
    [
        "Конс-2",
        "Подача25-2",
        "Петрова Анна Сергеевна",
        date(2025, 1, 15),
        2,
        "Польша, Латвия",
        "думает",
        50,
        "BYN",
        "да",
    ],
]
CASES = [
    [
        "Код кейса",
        "Код клиента",
        "Клиент",
        "Семья",
        "Код консультации",
        "Источник",
        "Страна визы",
        "Регистратор",
        "Номер в ВЦ",
        "Дата подачи",
        "Результат",
        "Кратность",
        "Дата решения",
        "Стоимость",
        "Талон",
        "Валюта талона",
        "Талон оплачен до CRM",
    ],
    [
        "Подача25-2",
        "Подача25-2",
        "Петрова Анна Сергеевна",
        "Петровы",
        "Конс-2",
        "Подача25",
        "Польша",
        "Иван",
        "PL123",
        date(2025, 5, 20),
        "виза",
        "многократная",
        date(2025, 6, 2),
        800,
        300,
        "PLN",
        "да",
    ],
    [
        "Подача25-3",
        "Подача25-3",
        "Петров Игорь",
        "Петровы",
        "Конс-2",
        "Подача25",
        "Польша",
        "Иван",
        None,
        date(2025, 5, 20),
        None,
        None,
        None,
        600,
        None,
        None,
        None,
    ],
    [
        "Франция-2",
        "Франция-2",
        "Сидоренко Ольга",
        None,
        None,
        "Франция",
        "Франция",
        None,
        None,
        None,
        None,
        None,
        None,
        500,
        None,
        None,
        None,
    ],
]
PAYMENTS = [
    ["Код кейса", "Клиент", "Дата", "Сумма", "Валюта", "Официальный"],
    ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 1, 15), 200, "BYN", "нет"],
    ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 5, 20), 150, "EUR", "да"],
]
EXPENSES = [
    ["Код кейса", "Дата", "Описание", "Сумма"],
    ["Подача25-2", date(2025, 5, 12), "Перевод справки", 30],
]
QUEUE = [
    ["Код кейса", "Клиент", "Дата постановки", "Дата письма"],
    ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 1, 20), date(2025, 5, 5)],
    ["Подача25-3", "Петров Игорь", date(2025, 1, 20), date(2025, 5, 5)],
]
ATTEMPTS = [
    ["Код кейса", "Номер попытки", "Дата отправки", "Последний день окна", "Итог", "Дата закрытия"],
    ["Франция-2", 1, date(2025, 2, 10), date(2025, 4, 11), "истекла", date(2025, 4, 11)],
    ["Франция-2", 2, date(2025, 4, 12), date(2025, 6, 11), None, None],
]
ACCOUNTS = [
    ["Код клиента", "Код кейса", "Система", "Логин", "Пароль", "Чья почта", "Создала сама"],
    ["Подача25-2", "Подача25-2", "Почта", "anna.p@example.com", "Pa55word", "клиента", "нет"],
    [None, None, "Booking.com", "booking.mine@example.com", "BkPass1", "моя резервная", "да"],
]
BOOKINGS = [
    ["Код кейса", "Место", "Заезд", "Выезд", "Бесплатная отмена до", "Номер брони", "Аккаунт"],
    [
        "Подача25-2",
        "Hotel Old Town, Kraków",
        date(2025, 6, 12),
        date(2025, 6, 19),
        date(2025, 6, 5),
        "4455667788",
        "booking.mine@example.com",
    ],
]
BOOK = {
    "Клиенты": CLIENTS,
    "Заметки": NOTES,
    "Консультации": CONSULTATIONS,
    "Кейсы": CASES,
    "Платежи": PAYMENTS,
    "Расходы": EXPENSES,
    "Очередь": QUEUE,
    "Заявки с окном": ATTEMPTS,
    "Учётные записи": ACCOUNTS,
    "Брони": BOOKINGS,
}


async def test_a_workbook_becomes_clients_cases_and_all_their_records(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)

    report = await run(client, BOOK)

    assert report["skipped_count"] == 0, report["skipped"]
    assert report["warnings_count"] == 0, report["warnings"]
    assert report["created"] == {
        "contacts": 3,
        "notes": 1,
        "consultations": 1,
        "cases": 3,
        "groups": 1,
        "finances": 3,
        "payments": 2,
        "expenses": 1,
        "queue_entries": 2,
        "attempts": 2,
        "vault_entries": 2,
        "passwords": 2,
        "bookings": 1,
        "channel": 1,
        "registrar": 1,
    }
    cases = await rows(
        owner_engine,
        "SELECT c.full_name, v.number, v.group_id, v.stage, v.outcome, v.visa_entries, "
        "v.consultation_id IS NOT NULL AS from_consultation, f.price, f.registrar_fee_amount, "
        "f.registrar_fee_currency, f.registrar_fee_base, f.registrar_fee_settled "
        "FROM visa_cases v JOIN contacts c ON c.id = v.contact_id "
        "LEFT JOIN case_finances f ON f.case_id = v.id WHERE v.tenant_id = :t ORDER BY v.number",
        t=tenant,
    )
    anna, igor, olga = cases
    assert anna["group_id"] is not None and anna["group_id"] == igor["group_id"]
    assert olga["group_id"] is None
    assert (anna["stage"], anna["outcome"], anna["visa_entries"]) == ("result", "visa", "multiple")
    assert igor["stage"] == "review" and olga["stage"] == "waiting"
    assert anna["from_consultation"] and igor["from_consultation"]
    # The talon in złoty at the National Bank rate, already paid to the registrar.
    assert (anna["registrar_fee_amount"], anna["registrar_fee_currency"]) == (300, "PLN")
    assert anna["registrar_fee_base"] == 240 and anna["registrar_fee_settled"] is True
    # The consultation the case came from is «yes» now, as when a case is opened by hand.
    [consultation] = await rows(
        owner_engine, "SELECT decision, fee_paid FROM consultations WHERE tenant_id = :t", t=tenant
    )
    assert consultation == {"decision": "yes", "fee_paid": True}
    payments = await rows(
        owner_engine,
        "SELECT amount, currency, amount_base, official FROM case_payments WHERE tenant_id = :t "
        "ORDER BY paid_on",
        t=tenant,
    )
    assert [(p["amount"], p["currency"], p["amount_base"], p["official"]) for p in payments] == [
        (200, "BYN", 200, False),
        (150, "EUR", 510, True),
    ]
    [booking] = await rows(
        owner_engine,
        "SELECT b.place, e.login FROM bookings b JOIN vault_entries e ON e.id = b.vault_entry_id "
        "WHERE b.tenant_id = :t",
        t=tenant,
    )
    assert booking["login"] == "booking.mine@example.com"
    attempts = await rows(
        owner_engine,
        "SELECT number, outcome FROM application_attempts WHERE tenant_id = :t ORDER BY number",
        t=tenant,
    )
    assert [(a["number"], a["outcome"]) for a in attempts] == [(1, "expired"), (2, None)]

    # The password is in the vault, encrypted, and comes back only when revealed.
    [entry] = await rows(
        owner_engine,
        "SELECT id, password_encrypted FROM vault_entries WHERE tenant_id = :t "
        "AND login = 'anna.p@example.com'",
        t=tenant,
    )
    assert b"Pa55word" not in entry["password_encrypted"]
    revealed = await ok(await client.post(f"/api/vault/entries/{entry['id']}/reveal"))
    assert revealed["password"] == "Pa55word"

    # One audit event with the counts, nothing from the file.
    [event] = await rows(
        owner_engine,
        "SELECT details::text AS details FROM audit_events WHERE tenant_id = :t "
        "AND action = 'imports.completed'",
        t=tenant,
    )
    for value in ("Петрова", "+375291112233", "Pa55word", "anna.p@example.com"):
        assert value not in event["details"]


async def test_check_writes_nothing_and_lists_the_unknown_list_values(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await check(client, {"Клиенты": CLIENTS, "Кейсы": CASES})

    assert report["id"] is None
    assert report["created"]["contacts"] == 3 and report["created"]["cases"] == 3
    assert report["unknown"] == {
        "channel": [{"key": "сарафан", "value": "Сарафан", "rows": 2}],
        "registrar": [{"key": "иван", "value": "Иван", "rows": 2}],
    }
    for table in ("contacts", "visa_cases", "lead_channels", "registrars", "import_runs"):
        assert await count(owner_engine, table, tenant) == 0


async def test_unknown_values_are_added_taken_from_the_list_or_left_empty(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    natalia = await ok(
        await client.post(
            "/api/dictionaries/registrars",
            json={"name": "Наталья", "kind": "individual", "currency": "BYN"},
        )
    )

    report = await run(
        client,
        {"Клиенты": CLIENTS, "Кейсы": CASES},
        choices={"channel": {"сарафан": "skip"}, "registrar": {"иван": natalia["id"]}},
    )

    assert "channel" not in report["created"] and "registrar" not in report["created"]
    assert await count(owner_engine, "lead_channels", tenant) == 0
    registrars = await rows(
        owner_engine,
        "SELECT DISTINCT r.name FROM visa_cases v JOIN registrars r ON r.id = v.registrar_id "
        "WHERE v.tenant_id = :t",
        t=tenant,
    )
    assert registrars == [{"name": "Наталья"}]
    assert {"sheet": "Клиенты", "row": 2, "column": "Канал прихода", "reason": "unknown_value"} in (
        report["warnings"]
    )


async def test_a_repeated_or_re_made_file_finds_the_records_it_made(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)
    counts = {
        table: await count(owner_engine, table, tenant)
        for table in (
            "contacts",
            "contact_notes",
            "consultations",
            "visa_cases",
            "submission_groups",
            "case_payments",
            "case_expenses",
            "waiting_list_entries",
            "application_attempts",
            "bookings",
            "vault_entries",
        )
    }

    again = await run(client, BOOK)
    assert again["created"] == {} and again["updated"] == {}

    # The AI made the file again with other codes: the same records, nothing added.
    renamed = {
        name: [
            [
                f"new-{c}"
                if isinstance(c, str) and "-" in c and c[0].isupper() and c.split("-")[-1].isdigit()
                else c
                for c in row
            ]
            for row in sheet
        ]
        for name, sheet in BOOK.items()
    }
    remade = await run(client, renamed)
    assert remade["skipped_count"] == 0, remade["skipped"]
    assert remade["created"] == {}
    for table, number in counts.items():
        assert await count(owner_engine, table, tenant) == number, table


async def test_people_are_found_by_phone_nick_or_a_unique_name_and_their_card_kept(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    for name, extra in (
        ({"last_name": "Петрова", "first_name": "Анна"}, {"phone": "+375291112233"}),
        ({"last_name": "Петров", "first_name": "Игорь"}, {"email": "igor@example.com"}),
        ({"last_name": "Сидоренко", "first_name": "Ольга"}, {"telegram": "olga_s"}),
    ):
        await ok(await client.post("/api/contacts", json=name | extra))

    report = await run(
        client,
        {
            "Клиенты": [
                ["Код клиента", "Фамилия", "Имя", "Телефон", "Telegram", "Email"],
                ["1", "Петрова-Смит", "Анна", "+375291112233", None, "anna@example.com"],
                ["2", "Петров", "Игорь", None, None, "other@example.com"],
                # The surname changed: the nick finds her card, since the given name fits.
                ["3", "Другая", "Ольга", None, "@olga_s", None],
            ]
        },
    )

    assert report["created"] == {} and report["updated"] == {"contacts": 1}
    contacts = await rows(
        owner_engine, "SELECT full_name, email FROM contacts WHERE tenant_id = :t", t=tenant
    )
    # A file from the AI only fills what the card does not have: names and emails stay.
    assert sorted(contacts, key=lambda c: c["full_name"]) == [
        {"full_name": "Петров Игорь", "email": "igor@example.com"},
        {"full_name": "Петрова Анна", "email": "anna@example.com"},
        {"full_name": "Сидоренко Ольга", "email": None},
    ]


async def test_an_empty_cell_of_a_file_from_the_ai_changes_nothing(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES})

    cases = [CASES[0], [*CASES[1][:8], None, *CASES[1][9:]]]
    report = await run(client, {"Клиенты": CLIENTS, "Кейсы": cases})

    assert report["updated"] == {}
    [case] = await rows(
        owner_engine,
        "SELECT vac_number FROM visa_cases WHERE tenant_id = :t AND vac_number IS NOT NULL",
        t=tenant,
    )
    assert case["vac_number"] == "PL123"


# Olga and her French case alone.
FRANCE = {"Клиенты": [CLIENTS[0], CLIENTS[3]], "Кейсы": [CASES[0], CASES[3]]}


async def _attempts(owner_engine: AsyncEngine, tenant: uuid.UUID) -> list[tuple]:
    found = await rows(
        owner_engine,
        "SELECT number, sent_on, deadline_on, outcome, closed_on FROM application_attempts "
        "WHERE tenant_id = :t ORDER BY number",
        t=tenant,
    )
    return [tuple(a.values()) for a in found]


async def test_the_crm_counts_the_window_and_closes_an_expired_attempt_on_its_last_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Backlog 1.25: the file leaves «Последний день окна» empty and the CRM puts the sent
    date plus the window of her direction (France from Belarus: 60 days). An expired attempt
    without «Дата закрытия» is closed on its last day, as «Отправить повторно» does; a filled
    day is taken as it is."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book = {
        **FRANCE,
        "Заявки с окном": [
            ATTEMPTS[0],
            ["Франция-2", 1, date(2025, 2, 10), None, "истекла", None],
            ["Франция-2", 2, date(2025, 4, 12), None, "отменена", None],
            ["Франция-2", 3, date(2025, 6, 1), date(2025, 7, 30), None, None],
        ],
    }

    report = await run(client, book)

    assert report["skipped"] == [] and report["warnings"] == []
    assert await _attempts(owner_engine, tenant) == [
        (1, date(2025, 2, 10), date(2025, 4, 11), "expired", date(2025, 4, 11)),
        (2, date(2025, 4, 12), date(2025, 6, 11), "cancelled", None),
        (3, date(2025, 6, 1), date(2025, 7, 30), None, None),
    ]
    again = await run(client, book)
    assert (again["created"], again["updated"], again["skipped"]) == ({}, {}, [])


async def test_an_attempt_in_the_crm_keeps_the_window_it_was_sent_with(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Her direction's window changed after the attempt was sent: the file again without
    «Последний день окна» does not move the deadline, and a corrected sent date moves it by
    the window the attempt was sent with, as in the CRM (decisions.md, 2026-11-04)."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    attempt = ["Франция-2", 1, date(2025, 2, 10), None, None, None]
    book = {**FRANCE, "Заявки с окном": [ATTEMPTS[0], attempt]}
    await run(client, book)
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    [france] = [
        r
        for r in (await client.get("/api/reference/rules")).json()
        if r["destination_id"] == countries["FR"]
    ]
    await ok(await client.patch(f"/api/reference/rules/{france['id']}", json={"window_days": 90}))

    again = await run(client, book)
    assert (again["updated"], again["skipped"]) == ({}, [])
    assert await _attempts(owner_engine, tenant) == [
        (1, date(2025, 2, 10), date(2025, 4, 11), None, None)
    ]

    corrected = [ATTEMPTS[0], ["Франция-2", 1, date(2025, 2, 11), None, None, None]]
    await run(client, {**book, "Заявки с окном": corrected})
    assert await _attempts(owner_engine, tenant) == [
        (1, date(2025, 2, 11), date(2025, 4, 12), None, None)
    ]


async def _exported(owner_engine: AsyncEngine, tenant: uuid.UUID) -> dict[str, dict]:
    """The CRM's own codes of the records, as an export writes them."""
    [anna] = await rows(
        owner_engine,
        "SELECT c.id AS contact, v.id AS case, v.updated_at FROM visa_cases v "
        "JOIN contacts c ON c.id = v.contact_id WHERE v.tenant_id = :t AND v.vac_number = 'PL123'",
        t=tenant,
    )
    payments = await rows(
        owner_engine,
        "SELECT id, amount FROM case_payments WHERE case_id = :c ORDER BY paid_on",
        c=anna["case"],
    )
    return {"anna": anna, "payments": payments}


def _about(at: datetime) -> list[list]:
    return [["Формат", "VisaCRM, версия 1"], ["Выгружено", at]]


def _minsk_now() -> datetime:
    """The export writes its time in the specialist's time zone, without the zone."""
    return datetime.now(ZoneInfo("Europe/Minsk")).replace(tzinfo=None, microsecond=0)


async def test_a_row_of_an_export_is_a_snapshot_of_its_record(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES, "Платежи": PAYMENTS})
    crm = await _exported(owner_engine, tenant)
    anna, first = crm["anna"], crm["payments"][0]
    later = _minsk_now() + timedelta(hours=1)

    report = await run(
        client,
        {
            "Кейсы": [
                ["Код кейса", "Код клиента", "Клиент", "Страна визы", "Номер в ВЦ", "Дата подачи"],
                [
                    str(anna["case"]),
                    str(anna["contact"]),
                    "Петрова Анна Сергеевна",
                    "Польша",
                    None,
                    date(2025, 5, 21),
                ],
            ],
            "Платежи": [
                ["Код", "Код кейса", "Дата", "Сумма", "Валюта"],
                [str(first["id"]), str(anna["case"]), date(2025, 1, 15), 250, "BYN"],
            ],
            "О файле": _about(later),
        },
    )

    assert report["skipped_count"] == 0, report["skipped"]
    assert report["created"] == {}
    assert report["updated"] == {"cases": 1, "payments": 1}
    [case] = await rows(
        owner_engine,
        "SELECT vac_number, submission_on FROM visa_cases WHERE id = :c",
        c=anna["case"],
    )
    # The cell left empty in the export clears the field.
    assert case == {"vac_number": None, "submission_on": date(2025, 5, 21)}
    [payment] = await rows(
        owner_engine, "SELECT amount FROM case_payments WHERE id = :p", p=first["id"]
    )
    assert payment["amount"] == 250


async def test_a_record_changed_in_the_crm_after_the_export_is_not_overwritten(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES})
    anna = (await _exported(owner_engine, tenant))["anna"]
    earlier = _minsk_now() - timedelta(days=1)

    report = await run(
        client,
        {
            "Кейсы": [
                ["Код кейса", "Код клиента", "Страна визы", "Номер в ВЦ"],
                [str(anna["case"]), str(anna["contact"]), "Польша", "PL999"],
            ],
            "О файле": _about(earlier),
        },
    )

    assert report["skipped"] == [
        {"sheet": "Кейсы", "row": 2, "column": None, "reason": "changed_after_export"}
    ]
    [case] = await rows(
        owner_engine, "SELECT vac_number FROM visa_cases WHERE id = :c", c=anna["case"]
    )
    assert case["vac_number"] == "PL123"


async def test_what_an_export_takes_off_is_not_filled_in_again(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Backlog 1.15.12: the talon, the prepayment, the consultation credit and the
    partner's terms taken off — here by the empty cells of an export loaded back — are not
    filled in again from the lists in the case finances (owner's answer 2026-11-24)."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES})
    anna = (await _exported(owner_engine, tenant))["anna"]
    [ivan] = [r for r in await ok(await client.get("/api/dictionaries/registrars"))]
    codes = {c["code"]: c["id"] for c in await ok(await client.get("/api/reference/countries"))}
    await ok(
        await client.put(
            f"/api/dictionaries/registrars/{ivan['id']}/prices",
            json=[
                {
                    "country_id": codes["PL"],
                    "purpose_id": None,
                    "hall": "standard",
                    "amount": "300",
                    "currency": "PLN",
                }
            ],
        )
    )
    await ok(
        await client.put(
            "/api/dictionaries/prepayments",
            json=[{"country_id": codes["PL"], "purpose_id": None, "amount": "150"}],
        )
    )
    url = f"/api/cases/{anna['case']}/finance"
    held = {"prepayment": "100", "consultation_credit": "20", "partner_terms": "none"}
    await ok(await client.patch(url, json=held))

    report = await run(
        client,
        {
            "Кейсы": [
                [
                    "Код кейса",
                    "Код клиента",
                    "Страна визы",
                    "Предоплата",
                    "Зачёт консультации",
                    "Талон",
                    "Условия партнёра",
                ],
                [str(anna["case"]), str(anna["contact"]), "Польша", None, None, None, None],
            ],
            "О файле": _about(_minsk_now() + timedelta(hours=1)),
        },
    )

    assert report["skipped_count"] == 0, report["skipped"]
    finance = await ok(await client.get(url))
    assert (finance["registrar_fee_amount"], finance["prepayment"]) == (None, None)
    assert (finance["consultation_credit"], finance["partner_terms"]) == ("0.00", None)
    assert finance["suggestions"]["registrar_fee"] is None
    assert finance["suggestions"]["prepayment"] is None
    [flags] = await rows(
        owner_engine,
        "SELECT registrar_fee_removed, prepayment_removed, consultation_credit_removed, "
        "partner_terms_removed FROM case_finances WHERE case_id = :c",
        c=anna["case"],
    )
    assert flags == dict.fromkeys(flags, True)


async def test_an_empty_password_cell_of_an_export_keeps_the_password(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """docs/data-format.md → «Пустая ячейка»: a row of an export clears the fields of its
    empty cells, but never the password, so an export without passwords loads back safely."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES, "Учётные записи": ACCOUNTS[:2]})
    [entry] = await rows(
        owner_engine,
        "SELECT id, contact_id, case_id FROM vault_entries WHERE tenant_id = :t "
        "AND login = 'anna.p@example.com'",
        t=tenant,
    )

    report = await run(
        client,
        {
            "Учётные записи": [
                ["Код", "Код клиента", "Код кейса", "Система", "Логин", "Пароль", "Чья почта"],
                [
                    str(entry["id"]),
                    str(entry["contact_id"]),
                    str(entry["case_id"]),
                    "Почта",
                    "anna.p@example.com",
                    None,
                    None,
                ],
            ],
            "О файле": _about(_minsk_now() + timedelta(hours=1)),
        },
    )

    assert report["skipped_count"] == 0, report["skipped"]
    # The row is a snapshot: its other empty cell cleared its field...
    assert report["updated"] == {"vault_entries": 1}
    [after] = await rows(
        owner_engine, "SELECT email_owner FROM vault_entries WHERE id = :e", e=entry["id"]
    )
    assert after["email_owner"] is None
    # ...and the password is the one the first file gave.
    revealed = await ok(await client.post(f"/api/vault/entries/{entry['id']}/reveal"))
    assert revealed["password"] == "Pa55word"


async def test_codes_of_another_workspace_find_nothing(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    other = await make_user("other@example.com")
    await login(client, "other@example.com")
    await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES})
    theirs = (await _exported(owner_engine, other))["anna"]
    client.cookies.clear()
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await run(
        client,
        {
            "Платежи": [
                ["Код кейса", "Дата", "Сумма", "Валюта"],
                [str(theirs["case"]), date(2025, 1, 15), 999, "BYN"],
            ],
            "Кейсы": [
                ["Код кейса", "Код клиента", "Страна визы"],
                [str(theirs["case"]), str(theirs["contact"]), "Польша"],
            ],
        },
    )

    # Their client code is no card here and the file has no client sheet: the case row is
    # skipped, and so is the payment of a case the file did not make.
    assert {(s["sheet"], s["reason"]) for s in report["skipped"]} == {
        ("Кейсы", "unknown_code"),
        ("Платежи", "unknown_code"),
    }
    assert await count(owner_engine, "case_payments", tenant) == 0
    assert await count(owner_engine, "case_payments", other) == 0
    [case] = await rows(
        owner_engine, "SELECT vac_number FROM visa_cases WHERE id = :c", c=theirs["case"]
    )
    assert case["vac_number"] == "PL123"


async def test_the_result_stage_without_a_result_is_left_to_the_dates(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """CRM audit 2026-11-18: «Результат» takes a case only with its result. A row that
    asks for it without one is warned of, and the case stands where its dates put it."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    cases = [
        ["Код кейса", "Код клиента", "Страна визы", "Дата подачи", "Результат", "Этап"],
        ["К-1", "Подача25-2", "Польша", date(2025, 5, 20), None, "результат"],
        ["К-2", "Подача25-3", "Польша", None, "отказ", "результат"],
    ]

    report = await run(client, {"Клиенты": CLIENTS, "Кейсы": cases})

    assert report["warnings"] == [
        {"sheet": "Кейсы", "row": 2, "column": "Этап", "reason": "needs_outcome"}
    ]
    stages = await rows(
        owner_engine,
        "SELECT stage, outcome FROM visa_cases WHERE tenant_id = :t ORDER BY number",
        t=tenant,
    )
    assert stages == [
        {"stage": "review", "outcome": None},
        {"stage": "result", "outcome": "refusal"},
    ]


async def test_a_case_priced_in_another_currency_keeps_it_unless_the_file_changes_the_price(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # The file has the price in the base currency (pilot fix 4): 1000 PLN at 0.80 is the
    # file's 800 BYN, so the same file changes nothing; another price puts it in BYN.
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)
    [case] = await rows(
        owner_engine,
        "SELECT f.case_id FROM case_finances f WHERE f.tenant_id = :t AND f.price = 800",
        t=tenant,
    )
    url = f"/api/cases/{case['case_id']}/finance"
    await ok(await client.patch(url, json={"currency": "PLN", "price": "1000"}))

    again = await run(client, BOOK)
    assert again["updated"] == {}
    kept = (await client.get(url)).json()
    assert (kept["currency"], kept["price"]) == ("PLN", "1000.00")

    repriced = {**BOOK, "Кейсы": [CASES[0], [*CASES[1][:13], 900, *CASES[1][14:]], *CASES[2:]]}
    await run(client, repriced)
    back = (await client.get(url)).json()
    assert (back["currency"], back["price"], back["rate"]) == ("BYN", "900.00", "1.000000")


async def test_a_row_whose_client_does_not_match_its_code_is_skipped(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await run(
        client,
        {
            "Клиенты": CLIENTS,
            "Заметки": [
                ["Код клиента", "Клиент", "Текст"],
                ["Подача25-2", "Петров Игорь", "чужая заметка"],
            ],
        },
    )

    assert report["skipped"] == [
        {"sheet": "Заметки", "row": 2, "column": "Клиент", "reason": "client_mismatch"}
    ]
    assert await count(owner_engine, "contact_notes", tenant) == 0


async def test_the_report_names_sheet_row_and_column_and_never_a_value(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await check(
        client,
        {
            "Клиенты": [
                ["Код клиента", "Фамилия", "Имя", "Телефон", "Дата рождения"],
                ["1", "Петрова", "Анна", "8 029 111-22-33", "12.03"],
                ["2", None, None, "+375297778899", None],
            ],
            "Отчёт": [["что угодно"]],
        },
    )

    assert report["skipped"] == [
        {"sheet": "Клиенты", "row": 3, "column": None, "reason": "no_name"}
    ]
    assert report["warnings"] == [
        {"sheet": "Клиенты", "row": 2, "column": "Телефон", "reason": "phone"},
        {"sheet": "Клиенты", "row": 2, "column": "Дата рождения", "reason": "date"},
        {"sheet": "Отчёт", "row": None, "column": None, "reason": "unknown_sheet"},
    ]
    for value in ("Петрова", "029", "12.03", "+375297778899"):
        assert value not in str(report)


async def test_a_sheet_of_a_module_the_workspace_has_not_is_passed_by(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET modules = array_remove(modules, 'finances') WHERE id = :t"),
            {"t": tenant},
        )
    await login(client, "spec@example.com")

    report = await run(client, {"Клиенты": CLIENTS, "Кейсы": CASES, "Платежи": PAYMENTS})

    assert {"sheet": "Платежи", "row": None, "column": None, "reason": "module_disabled"} in (
        report["skipped"]
    )
    assert report["created"]["cases"] == 3
    assert await count(owner_engine, "case_finances", tenant) == 0


async def test_only_the_specialist_imports_and_files_are_bounded(
    client: AsyncClient, make_user: MakeUser
):
    tenant = await make_user("spec@example.com")
    await make_user("emp@example.com", role="employee", tenant_id=tenant)
    await login(client, "spec@example.com")

    unreadable = {"file_name": "x.xlsx", "content": base64.b64encode(b"name;phone").decode()}
    response = await client.post(f"{API}/uploads", json=unreadable)
    assert response.status_code == 422 and response.json()["detail"] == "file_unreadable"

    client.cookies.clear()
    await login(client, "emp@example.com")
    response = await client.post(f"{API}/uploads", json=body({"Клиенты": CLIENTS}))
    assert response.status_code == 403


async def test_the_history_keeps_each_run_with_its_report(client: AsyncClient, make_user: MakeUser):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    report = await run(client, {"Клиенты": CLIENTS})

    [summary] = await ok(await client.get(API))
    assert summary["id"] == report["id"] and summary["kind"] == "workbook"
    assert summary["imported"] == 3
    again = await ok(await client.get(f"{API}/{report['id']}"))
    assert again["created"] == {"contacts": 3, "channel": 1}


async def test_the_template_has_every_sheet_and_loads_back_empty(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await ok(await client.post("/api/dictionaries/channels", json={"name": "Instagram"}))

    response = await client.get(f"{API}/template")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == [
        "Клиенты",
        "Заметки",
        "Консультации",
        "Кейсы",
        "Платежи",
        "Расходы",
        "Очередь",
        "Заявки с окном",
        "Брони",
        "Учётные записи",
        "Справочник",
        "О файле",
    ]
    assert [c.value for c in book["Клиенты"][1]][:3] == ["Код клиента", "Фамилия", "Имя"]
    reference = [c.value for column in book["Справочник"].iter_cols() for c in column]
    assert "Instagram" in reference and "Польша" in reference and "многократная" in reference

    content = base64.b64encode(response.content).decode()
    report = await import_report(client, {"file_name": "t.xlsx", "content": content}, save=False)
    assert report["rows"] == 0 and report["skipped"] == [] and report["warnings"] == []


async def test_the_prompt_comes_in_both_languages_with_the_format_columns(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    ru = await ok(await client.get(f"{API}/prompt", params={"language": "ru"}))
    en = await ok(await client.get(f"{API}/prompt", params={"language": "en"}))

    assert "Клиенты: Код клиента* | Фамилия | Имя" in ru["text"]
    assert "Clients: Client code* | Last name | First name" in en["text"]


async def test_a_city_of_her_direction_is_found_without_an_english_name(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    [poland] = [
        r
        for r in (await client.get("/api/reference/rules")).json()
        if r["destination_id"] == countries["PL"]
    ]
    await ok(
        await client.post(
            f"/api/reference/rules/{poland['id']}/visa-centers", json={"cities": ["Минск"]}
        )
    )

    report = await run(
        client,
        {
            "Клиенты": CLIENTS[:2],
            "Кейсы": [
                ["Код кейса", "Код клиента", "Страна визы", "Город подачи"],
                ["k1", "Подача25-2", "Польша", "минск"],
            ],
        },
    )

    assert report["warnings"] == []
    [case] = await rows(
        owner_engine,
        "SELECT c.city_ru FROM visa_cases v JOIN visa_centers c ON c.id = v.visa_center_id "
        "WHERE v.tenant_id = :t",
        t=tenant,
    )
    assert case == {"city_ru": "Минск"}
    # The English template names the city as she wrote it.
    response = await client.get(f"{API}/template", params={"language": "en"})
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert "Минск" in [c.value for column in book["Reference"].iter_cols() for c in column]


# --- Rates and currencies on a repeated upload (backlog A4) --------------------------------

# Anna's case with a payment in euros, an expense in dollars and a talon in złoty; no rates.
MONEY_EXPENSES = [
    ["Код кейса", "Дата", "Описание", "Сумма", "Валюта"],
    ["Подача25-2", date(2025, 5, 12), "Перевод справки", 10, "USD"],
]
MONEY = {"Клиенты": CLIENTS, "Кейсы": CASES, "Платежи": PAYMENTS, "Расходы": MONEY_EXPENSES}


def site_rates(monkeypatch: pytest.MonkeyPatch, **byn: str) -> None:
    """The site has downloaded other rates of the National Bank since."""
    held = {currency: (date(2026, 11, 27), Decimal(rate)) for currency, rate in byn.items()}
    monkeypatch.setattr(rates, "_latest", held)


async def money_kept(owner_engine: AsyncEngine, tenant: uuid.UUID) -> dict[str, list[tuple]]:
    """Rates and sums in the accounting currency of what is in another currency."""
    payments = await rows(
        owner_engine,
        "SELECT amount, currency, rate, amount_base FROM case_payments "
        "WHERE tenant_id = :t AND currency <> 'BYN' ORDER BY paid_on",
        t=tenant,
    )
    expenses = await rows(
        owner_engine,
        "SELECT amount, currency, rate, amount_base FROM case_expenses WHERE tenant_id = :t",
        t=tenant,
    )
    talons = await rows(
        owner_engine,
        "SELECT registrar_fee_amount, registrar_fee_currency, registrar_fee_rate, "
        "registrar_fee_base FROM case_finances "
        "WHERE tenant_id = :t AND registrar_fee_amount IS NOT NULL",
        t=tenant,
    )
    return {
        "payments": [tuple(p.values()) for p in payments],
        "expenses": [tuple(e.values()) for e in expenses],
        "talons": [tuple(f.values()) for f in talons],
    }


async def test_a_repeated_file_keeps_the_rate_of_what_it_already_loaded(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, MONEY)
    assert await money_kept(owner_engine, tenant) == {
        "payments": [(150, "EUR", Decimal("3.4"), 510)],
        "expenses": [(10, "USD", Decimal("3.1"), 31)],
        "talons": [(300, "PLN", Decimal("0.8"), 240)],
    }
    # She corrected the rate of the payment in the case (to the rate of her bank).
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE case_payments SET rate = 3.25, amount_base = 487.50 "
                "WHERE tenant_id = :t AND currency = 'EUR'"
            ),
            {"t": tenant},
        )
    site_rates(monkeypatch, EUR="3.5", USD="3.2", PLN="0.9")

    again = await run(client, MONEY)

    assert again["created"] == {} and again["updated"] == {}
    assert again["skipped_count"] == 0, again["skipped"]
    assert await money_kept(owner_engine, tenant) == {
        "payments": [(150, "EUR", Decimal("3.25"), Decimal("487.50"))],
        "expenses": [(10, "USD", Decimal("3.1"), 31)],
        "talons": [(300, "PLN", Decimal("0.8"), 240)],
    }


async def test_a_repeated_file_needs_no_rate_for_what_it_does_not_change(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, MONEY)
    before = await money_kept(owner_engine, tenant)
    # The site has no rates at the moment: nothing of the file needs one.
    monkeypatch.setattr(rates, "_latest", {})

    again = await run(client, MONEY)

    assert again["skipped_count"] == 0, again["skipped"]
    assert "rate_unavailable" not in {issue["reason"] for issue in again["warnings"]}
    assert again["updated"] == {}
    assert await money_kept(owner_engine, tenant) == before


async def test_a_new_record_takes_the_latest_rate_and_a_rate_in_the_file_is_applied(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, MONEY)
    site_rates(monkeypatch, EUR="3.5", USD="3.2", PLN="0.9")

    payments = [
        [*PAYMENTS[0], "Курс"],
        [*PAYMENTS[1], None],
        # The file says the rate of the payment that is in the CRM: it is applied.
        [*PAYMENTS[2], Decimal("3.3")],
        # A payment the CRM has not: the latest rate of the site.
        ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 6, 1), 100, "EUR", "нет", None],
    ]
    expenses = [[*MONEY_EXPENSES[0], "Курс"], [*MONEY_EXPENSES[1], Decimal("3.05")]]
    cases = [
        [*CASES[0], "Курс талона"],
        [*CASES[1], Decimal("0.85")],
        *([*row, None] for row in CASES[2:]),
    ]
    report = await run(
        client, {"Клиенты": CLIENTS, "Кейсы": cases, "Платежи": payments, "Расходы": expenses}
    )

    assert report["skipped_count"] == 0, report["skipped"]
    assert report["created"] == {"payments": 1}
    assert report["updated"] == {"payments": 1, "expenses": 1, "finances": 1}
    assert await money_kept(owner_engine, tenant) == {
        "payments": [(150, "EUR", Decimal("3.3"), 495), (100, "EUR", Decimal("3.5"), 350)],
        "expenses": [(10, "USD", Decimal("3.05"), Decimal("30.50"))],
        "talons": [(300, "PLN", Decimal("0.85"), 255)],
    }


async def test_a_changed_sum_or_currency_is_counted_at_the_latest_rate(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """The record's own rate was the rate of another sum: the file gives none, so the
    site's latest one is taken, as for a new record (docs/decisions.md)."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, MONEY)
    site_rates(monkeypatch, EUR="3.5", USD="3.2", PLN="0.9")

    # The talon is 350 złoty now, and the expense was in euros, not dollars.
    talon = CASES[0].index("Талон")
    cases = [CASES[0], [*CASES[1][:talon], 350, *CASES[1][talon + 1 :]], *CASES[2:]]
    expenses = [MONEY_EXPENSES[0], [*MONEY_EXPENSES[1][:4], "EUR"]]
    report = await run(
        client, {"Клиенты": CLIENTS, "Кейсы": cases, "Платежи": PAYMENTS, "Расходы": expenses}
    )

    assert report["skipped_count"] == 0, report["skipped"]
    assert report["updated"] == {"expenses": 1, "finances": 1}
    assert await money_kept(owner_engine, tenant) == {
        "payments": [(150, "EUR", Decimal("3.4"), 510)],
        "expenses": [(10, "EUR", Decimal("3.5"), 35)],
        "talons": [(350, "PLN", Decimal("0.9"), 315)],
    }


async def test_an_unknown_currency_never_becomes_the_accounting_currency(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    currency = CASES[0].index("Валюта талона")
    fee = CONSULTATIONS[0].index("Валюта")
    book = {
        "Клиенты": CLIENTS,
        "Консультации": [
            CONSULTATIONS[0],
            [*CONSULTATIONS[1][:fee], "руб.", *CONSULTATIONS[1][fee + 1 :]],
        ],
        "Кейсы": [CASES[0], [*CASES[1][:currency], "RUB", *CASES[1][currency + 1 :]], *CASES[2:]],
        "Платежи": [
            PAYMENTS[0],
            ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 1, 15), 5000, "RUB", "нет"],
            ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 1, 16), 100, "евро", "нет"],
            # An empty «Валюта» is the accounting currency, as the format says.
            ["Подача25-2", "Петрова Анна Сергеевна", date(2025, 1, 17), 70, None, "нет"],
        ],
        "Расходы": [MONEY_EXPENSES[0], [*MONEY_EXPENSES[1][:3], 3000, "RUB"]],
    }

    report = await run(client, book)

    # The rows of the payments and the expense are not loaded, and the report says why.
    refused = [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report["skipped"]]
    assert refused == [
        ("currency", "Платежи", 2, "Валюта"),
        ("currency", "Платежи", 3, "Валюта"),
        ("currency", "Расходы", 2, "Валюта"),
    ]
    assert report["created"]["payments"] == 1 and "expenses" not in report["created"]
    payments = await rows(
        owner_engine,
        "SELECT amount, currency, rate, amount_base FROM case_payments WHERE tenant_id = :t",
        t=tenant,
    )
    assert [tuple(p.values()) for p in payments] == [(70, "BYN", 1, 70)]
    assert await count(owner_engine, "case_expenses", tenant) == 0
    # The talon and the fee of the consultation are left out; their rows are loaded.
    assert await money_kept(owner_engine, tenant) == {"payments": [], "expenses": [], "talons": []}
    [consultation] = await rows(
        owner_engine,
        "SELECT fee_amount, fee_currency, fee_paid FROM consultations WHERE tenant_id = :t",
        t=tenant,
    )
    assert consultation == {"fee_amount": None, "fee_currency": None, "fee_paid": True}
    warned = [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report["warnings"]]
    assert ("currency", "Кейсы", 2, "Талон") in warned
    assert ("currency", "Консультации", 2, "Стоимость") in warned


async def test_an_unknown_currency_does_not_touch_the_talon_and_the_fee_in_the_crm(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book = {"Клиенты": CLIENTS, "Консультации": CONSULTATIONS, "Кейсы": CASES}
    await run(client, book)
    currency = CASES[0].index("Валюта талона")
    fee = CONSULTATIONS[0].index("Валюта")

    report = await run(
        client,
        book
        | {
            "Консультации": [
                CONSULTATIONS[0],
                [*CONSULTATIONS[1][:fee], "евро", *CONSULTATIONS[1][fee + 1 :]],
            ],
            "Кейсы": [
                CASES[0],
                [*CASES[1][:currency], "злотые", *CASES[1][currency + 1 :]],
                *CASES[2:],
            ],
        },
    )

    assert report["updated"] == {}
    assert (await money_kept(owner_engine, tenant))["talons"] == [(300, "PLN", Decimal("0.8"), 240)]
    [consultation] = await rows(
        owner_engine,
        "SELECT fee_amount, fee_currency FROM consultations WHERE tenant_id = :t",
        t=tenant,
    )
    assert consultation == {"fee_amount": 50, "fee_currency": "BYN"}


# --- A5: a repeated file finds namesakes, a new case never opens in the future, a booking
# takes its own client's account.

NAMESAKE_RECORDS = ("contacts", "visa_cases", "case_payments", "waiting_list_entries")


async def namesake_counts(owner_engine: AsyncEngine, tenant: uuid.UUID) -> dict[str, int]:
    return {table: await count(owner_engine, table, tenant) for table in NAMESAKE_RECORDS}


def namesakes(phone: str | None = None) -> dict[str, list[list]]:
    """Three Anna Ivanovas: two with their phones, the third without any — her case, payment
    and queue entry hang on a card the row cannot be told by."""
    return {
        "Клиенты": [
            ["Код клиента", "Фамилия", "Имя", "Телефон"],
            ["К-1", "Иванова", "Анна", "+375291112233"],
            ["К-2", "Иванова", "Анна", "+375297778899"],
            ["К-3", "Иванова", "Анна", phone],
        ],
        "Кейсы": [
            ["Код кейса", "Код клиента", "Клиент", "Источник", "Страна визы"],
            ["Кейс-3", "К-3", "Иванова Анна", "Подача25", "Польша"],
        ],
        "Платежи": [
            ["Код кейса", "Дата", "Сумма", "Валюта"],
            ["Кейс-3", date(2025, 1, 15), 200, "BYN"],
        ],
        "Очередь": [["Код кейса", "Дата постановки"], ["Кейс-3", date(2025, 1, 20)]],
    }


async def test_a_book_with_namesakes_loaded_three_times_adds_nothing(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    first = await run(client, namesakes())
    assert first["created"]["contacts"] == 3
    made = await namesake_counts(owner_engine, tenant)
    assert made == {
        "contacts": 3,
        "visa_cases": 1,
        "case_payments": 1,
        "waiting_list_entries": 1,
    }

    for _ in range(2):
        again = await run(client, namesakes())
        assert (again["created"], again["updated"], again["skipped"]) == ({}, {}, [])
        assert await namesake_counts(owner_engine, tenant) == made


async def test_a_namesake_whose_phone_turned_up_fills_her_own_card(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, namesakes())
    await run(client, namesakes())

    report = await run(client, namesakes(phone="+375295554433"))

    assert report["created"] == {} and report["updated"] == {"contacts": 1}
    assert (await namesake_counts(owner_engine, tenant))["contacts"] == 3
    [anna] = await rows(
        owner_engine,
        "SELECT c.phone FROM contacts c JOIN visa_cases v ON v.contact_id = c.id "
        "WHERE c.tenant_id = :t",
        t=tenant,
    )
    assert anna["phone"] == "+375295554433"


async def test_a_family_member_without_her_own_phone_is_found_again(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The mother's row has the family phone, her daughter's none; two other Anna Petrovas
    with their own phones are clients too. The daughter's card and case are found every
    time."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book = {
        "Клиенты": [
            ["Код клиента", "Фамилия", "Имя", "Телефон"],
            ["Ч-1", "Петрова", "Анна", "+375297778899"],
            ["Ч-2", "Петрова", "Анна", "+375295554433"],
            ["М-1", "Петрова", "Ольга", "+375291112233"],
            ["М-2", "Петрова", "Анна", None],
        ],
        "Кейсы": [
            ["Код кейса", "Код клиента", "Клиент", "Семья", "Источник", "Страна визы"],
            ["М-1", "М-1", "Петрова Ольга", "Петровы", "Подача25", "Польша"],
            ["М-2", "М-2", "Петрова Анна", "Петровы", "Подача25", "Польша"],
        ],
    }
    await run(client, book)
    tables = ("contacts", "visa_cases", "submission_groups")
    made = {table: await count(owner_engine, table, tenant) for table in tables}
    assert made == {"contacts": 4, "visa_cases": 2, "submission_groups": 1}

    for _ in range(2):
        again = await run(client, book)
        assert (again["created"], again["updated"], again["skipped"]) == ({}, {}, [])
        assert {table: await count(owner_engine, table, tenant) for table in tables} == made


def _opening(**dates: date | None) -> dict[str, list[list]]:
    columns = {"submission_on": "Дата подачи", "questionnaire_due_on": "Анкета до"}
    return {
        "Клиенты": [["Код клиента", "Фамилия", "Имя"], ["К-1", "Иванова", "Анна"]],
        "Кейсы": [
            ["Код кейса", "Код клиента", "Клиент", "Страна визы", *(columns[k] for k in dates)],
            ["Кейс-1", "К-1", "Иванова Анна", "Польша", *dates.values()],
        ],
    }


async def _opened_on(owner_engine: AsyncEngine, tenant: uuid.UUID, zone: str) -> date:
    [case] = await rows(
        owner_engine, "SELECT created_at FROM visa_cases WHERE tenant_id = :t", t=tenant
    )
    return case["created_at"].astimezone(ZoneInfo(zone)).date()


@pytest.mark.parametrize(
    "dates",
    [
        {"submission_on": 40},
        {"questionnaire_due_on": 10},
        {"questionnaire_due_on": -10, "submission_on": 30},
    ],
    ids=["submission-ahead", "questionnaire-due-ahead", "questionnaire-due-is-a-deadline"],
)
async def test_a_new_case_without_an_opening_date_opens_today_at_the_latest(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine, dates: dict[str, int]
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    today = workspace_today()

    await run(client, _opening(**{k: today + timedelta(days=d) for k, d in dates.items()}))

    assert await _opened_on(owner_engine, tenant, "Europe/Minsk") == today


async def test_a_new_case_opens_on_the_specialists_today_west_of_utc(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """22:00 in New York is already tomorrow in UTC, and midnight UTC of her day is still the
    day before for her: the case opens on her own today either way."""
    tenant = await make_user("spec@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET timezone = 'America/New_York' WHERE id = :t"), {"t": tenant}
        )
    await login(client, "spec@example.com")
    zone = ZoneInfo("America/New_York")
    midnight = datetime.combine(WALL_CLOCK.now().date(), datetime.min.time(), zone)
    WALL_CLOCK.at = (midnight + timedelta(hours=22)).astimezone(UTC)
    today = WALL_CLOCK.at.astimezone(zone).date()
    assert WALL_CLOCK.at.date() == today + timedelta(days=1)

    await run(client, _opening(submission_on=today + timedelta(days=40)))
    assert await _opened_on(owner_engine, tenant, "America/New_York") == today
    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM visa_cases WHERE tenant_id = :t"), {"t": tenant})

    await run(client, _opening())
    assert await _opened_on(owner_engine, tenant, "America/New_York") == today


ANNA_LOGIN = "anna@example.com"


def _accounts_and_bookings(*bookings: list) -> dict[str, list[list]]:
    """Anna's mail and Booking account share a login; Igor has an account of his own."""
    return {
        "Клиенты": [CLIENTS[0], CLIENTS[1], CLIENTS[2]],
        "Кейсы": [CASES[0], CASES[1], CASES[2]],
        "Учётные записи": [
            ["Код клиента", "Код кейса", "Система", "Логин"],
            ["Подача25-2", None, "Почта", ANNA_LOGIN],
            ["Подача25-2", None, "Booking.com", ANNA_LOGIN],
            ["Подача25-3", None, "Booking.com", "igor@example.com"],
        ],
        "Брони": [["Код кейса", "Место", "Заезд", "Аккаунт"], *bookings],
    }


async def _booked(owner_engine: AsyncEngine, tenant: uuid.UUID) -> dict[str, tuple | None]:
    found = await rows(
        owner_engine,
        "SELECT b.place, s.name, e.login, c.full_name FROM bookings b "
        "LEFT JOIN vault_entries e ON e.id = b.vault_entry_id "
        "LEFT JOIN vault_systems s ON s.id = e.system_id "
        "LEFT JOIN contacts c ON c.id = e.contact_id WHERE b.tenant_id = :t",
        t=tenant,
    )
    return {
        b["place"]: (b["name"], b["login"], b["full_name"]) if b["login"] else None for b in found
    }


async def test_a_booking_takes_the_booking_account_of_its_own_client(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    book = _accounts_and_bookings(
        ["Подача25-2", "Hotel Kraków", date(2025, 6, 12), ANNA_LOGIN],
        # Igor's login on Anna's booking: not his account, and not hers.
        ["Подача25-2", "Hotel Gdańsk", date(2025, 6, 20), "igor@example.com"],
    )

    report = await run(client, book)

    assert await _booked(owner_engine, tenant) == {
        "Hotel Kraków": ("Booking.com", ANNA_LOGIN, "Петрова Анна Сергеевна"),
        "Hotel Gdańsk": None,
    }
    warned = [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report["warnings"]]
    assert ("unknown_value", "Брони", 3, "Аккаунт") in warned


async def test_a_booking_keeps_the_account_it_was_given_by_hand(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    book = _accounts_and_bookings(["Подача25-2", "Hotel Kraków", date(2025, 6, 12), ANNA_LOGIN])
    await run(client, book)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE bookings SET vault_entry_id = (SELECT e.id FROM vault_entries e "
                "JOIN vault_systems s ON s.id = e.system_id WHERE s.name = 'Почта' "
                "AND e.tenant_id = :t) WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )

    again = await run(client, book)

    assert again["updated"] == {}
    assert (await _booked(owner_engine, tenant))["Hotel Kraków"][0] == "Почта"


async def test_a_login_of_several_accounts_of_one_kind_leaves_the_booking_without_one(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await ok(
        await client.post(
            "/api/dictionaries/vault-systems", json={"name": "Airbnb", "kind": "booking"}
        )
    )
    book = _accounts_and_bookings(["Подача25-2", "Hotel Kraków", date(2025, 6, 12), ANNA_LOGIN])
    book["Учётные записи"].append(["Подача25-2", None, "Airbnb", ANNA_LOGIN])

    report = await run(client, book)

    assert await _booked(owner_engine, tenant) == {"Hotel Kraków": None}
    warned = [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report["warnings"]]
    assert ("account_ambiguous", "Брони", 2, "Аккаунт") in warned


async def test_an_account_of_another_workspace_is_never_a_bookings_account(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("other@example.com")
    await login(client, "other@example.com")
    await mail_system(client)
    await run(client, _accounts_and_bookings())
    client.cookies.clear()
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book = _accounts_and_bookings(["Подача25-2", "Hotel Kraków", date(2025, 6, 12), ANNA_LOGIN])
    del book["Учётные записи"]

    report = await run(client, book)

    assert await _booked(owner_engine, tenant) == {"Hotel Kraków": None}
    warned = [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report["warnings"]]
    assert ("unknown_value", "Брони", 2, "Аккаунт") in warned


async def test_a_booking_given_another_clients_account_before_takes_its_own(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """An earlier upload put Anna's booking on Igor's account of the same login: the next
    one moves it to her own."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    book = _accounts_and_bookings(["Подача25-2", "Hotel Kraków", date(2025, 6, 12), ANNA_LOGIN])
    book["Учётные записи"].append(["Подача25-3", None, "Booking.com", ANNA_LOGIN])
    await run(client, book)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE bookings SET vault_entry_id = (SELECT e.id FROM vault_entries e "
                "JOIN contacts c ON c.id = e.contact_id WHERE c.full_name = 'Петров Игорь' "
                "AND e.login = :login) WHERE tenant_id = :t"
            ),
            {"t": tenant, "login": ANNA_LOGIN},
        )

    again = await run(client, book)

    assert again["updated"] == {"bookings": 1}
    assert await _booked(owner_engine, tenant) == {
        "Hotel Kraków": ("Booking.com", ANNA_LOGIN, "Петрова Анна Сергеевна")
    }

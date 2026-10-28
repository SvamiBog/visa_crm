"""Импорт под таблицы визовика (backlog 1.16): the sheets as the first specialist keeps
them (the owner's description of 18.10.2026, with made-up people) — two years of
«Подача» in one book, consultations with a month instead of a day, the Polish queue saved
as text, France sent again and again, Italy's new visa center, bookings «до …»."""

import base64
from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login
from tests.test_imports import API, body, count, csv_file, ok, one, xlsx_file


def tsv_file(rows: list[list[str]]) -> str:
    return base64.b64encode("\n".join("\t".join(r) for r in rows).encode()).decode()


async def system(client: AsyncClient, kind: str, name: str) -> dict:
    systems = await ok(await client.get("/api/dictionaries/vault-systems"))
    return next((s for s in systems if s["kind"] == kind), None) or await ok(
        await client.post("/api/dictionaries/vault-systems", json={"name": name, "kind": kind})
    )


async def preview_of(client: AsyncClient, kind: str, name: str, content: str, **extra) -> dict:
    return await ok(await client.post(f"{API}/preview", json=body(kind, name, content) | extra))


async def rows(engine: AsyncEngine, sql: str, **params) -> list:
    async with engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).mappings().all())


SUBMISSION_2026 = [
    "",
    "ФИО",
    "email",
    "email создала я",
    "телефон при регистрации",
    "Телеграм",
    "ДР",
    "Откуда",
    "Страна",
    "Цель",
    "Посредник",
    "Дата подачи",
    "Результат",
    "Стоимость",
    "Предоплата",
    "Остаток",
    "Оплата за талон",
    "Оплата процента",
    "Прибыль",
    "код пользователя Греция",
    "Постановка в очередь",
    "Письмо с датой",
    "Дата подачи",
]


def submission_2026() -> list[list]:
    d = datetime
    return [
        SUBMISSION_2026,
        # Came again («повторно»): one card; the mailbox made for her holds its password.
        [1, "Иванова Анна (повторно)", "anna@mail.ru", "anna.visa@gmail.com Pa55word",
         "+375 29 111-11-11", "-", d(1990, 5, 1), "Сарафанка", "Польша", "Туризм", "Наталья",
         d(2026, 6, 20), "Мульти", 400, 400, 0, 120, 0, 280, None, "01.03.2026",
         d(2026, 6, 1), d(2026, 6, 20)],
        # A note after the name; «я» registered him: no registrar, so no talon.
        [2, "Петров Пётр от Ольги Т.", None, None, "80291112233", "нет", d(2030, 1, 1),
         "PK doc", "Греция", "Туризм", "я", None, None, 300, 100, 200, 50, 30, 220, "AB12",
         "15.03.2026", None, None],
        # A remark instead of a nick does not make two people one.
        [3, "Сидоров Олег", None, None, None, "в вайбере", None, "Сарафанка", "Польша",
         "Туризм", "Наталья В.", None, None, 350, 0, 350, 120, 0, 230, None, "октябрь 2025",
         None, None],
        [4, "Сидоров Иван", None, None, None, "в вайбере", None, "-", "Польша", "Туризм", "-",
         None, None, 0, 0, 0, 0, 0, 0, None, None, None, None],
        # Placeholder rows after the last client and the totals.
        [5, None, None, None, None, None, None, None, None, None, None, None, None, 0, 0, 0, 0,
         0, 0, None, None, None, None],
        [None, None, None, None, None, None, None, None, None, None, None, None, None, 1050, 500,
         550, 290, 30, 730, None, None, None, None],
    ]  # fmt: skip


SUBMISSION_2025 = [
    "",
    "ФИО",
    "email",
    "email создала я",
    "телефон при регистрации",
    "Телеграм",
    "ДР",
    "Откуда",
    "Страна",
    "Цель",
    "Посредник",
    "Дата подачи",
    "Результат",
    "Стоимость",
    "Предоплата",
    "Остаток",
    "Оплата моя посредникам",
    "Остаток после оплаты посредникам",
]


async def test_submission_sheets_of_two_years(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    mail = await system(client, "email", "Почта")
    book = xlsx_file(
        {
            "Подача26": submission_2026(),
            "Подача25": [
                SUBMISSION_2025,
                [
                    1,
                    "Иванова Анна",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "Сарафанка",
                    "Польша",
                    "Туризм",
                    "Наталья",
                    datetime(2025, 7, 1),
                    "Однократка",
                    350,
                    350,
                    0,
                    100,
                    250,
                ],
            ],  # fmt: skip
        }
    )

    preview = await preview_of(client, "cases", "Учёт.xlsx", book)
    fields = {c["header"] or f"#{c['index']}": c["field"] for c in preview["columns"]}
    assert fields["#0"] is None  # the row number
    assert fields["Посредник"] == "registrar" and fields["Предоплата"] == "paid"
    assert fields["Оплата за талон"] == "registrar_fee"
    assert fields["Оплата процента"] == "partner_payout"
    assert fields["email создала я"] == "own_email_login"
    assert fields["телефон при регистрации"] == "phone"
    assert fields["код пользователя Греция"] == "vac_number"
    assert fields["Постановка в очередь"] == "queued_on"
    assert fields["Остаток"] is None and fields["Прибыль"] is None
    # The first «Дата подачи» is the submission; the second stays out.
    dates = [c["field"] for c in preview["columns"] if c["header"] == "Дата подачи"]
    assert dates == ["submission_on", None]
    # «почта пароль» in a mailbox column is not shown back.
    own = next(c for c in preview["columns"] if c["header"] == "email создала я")
    assert own["samples"] == ["anna.visa@gmail.com ••••••"]

    request = body("cases", "Учёт.xlsx", book, preview)
    checked = await ok(await client.post(f"{API}/check", json=request))
    # The placeholder row is not counted; the totals row has no name.
    assert checked["rows"] == 5 and checked["imported"] == 4
    assert checked["skipped"] == [{"row": 7, "reason": "no_name", "column": None}]
    assert {u["value"] for u in checked["unknown"]["registrar"]} == {"Наталья", "Наталья В."}
    assert {u["value"] for u in checked["unknown"]["channel"]} == {"Сарафанка", "PK doc"}
    # «Наталья В.» is the same registrar; «PK doc» is a partner, not a channel.
    request["values"] = {
        "registrar": {"наталья в": "as:Наталья"},
        "channel": {"pk doc": "partner"},
    }
    report = await ok(await client.post(API, json=request))
    assert report["created"]["contacts"] == 4
    assert report["created"]["registrar"] == 1
    assert report["created"]["partner"] == 1 and report["created"]["channel"] == 1
    warnings = {(w["row"], w["column"], w["reason"]) for w in report["warnings"]}
    assert (3, "ДР", "unlikely_birth_date") in warnings
    assert (3, "Оплата за талон", "talon_without_registrar") in warnings
    assert (3, "Постановка в очередь", "not_a_queue") in warnings  # Greece: no waiting list
    assert (4, "Постановка в очередь", "date_month_only") in warnings

    anna = await one(
        owner_engine,
        "SELECT id, full_name, email, telegram FROM contacts WHERE full_name = 'Иванова Анна'",
    )
    assert (anna["email"], anna["telegram"]) == ("anna@mail.ru", None)
    case = await one(
        owner_engine,
        "SELECT c.id, c.application_email, c.application_email_owner, c.registrar_id, "
        "f.price, f.registrar_fee_amount, f.registrar_fee_on, f.registrar_fee_settled "
        "FROM visa_cases c JOIN case_finances f ON f.case_id = c.id WHERE c.contact_id = :c",
        c=anna["id"],
    )
    assert case["application_email"] == "anna.visa@gmail.com"
    assert case["application_email_owner"] == "specialist"
    assert (str(case["price"]), str(case["registrar_fee_amount"])) == ("400.00", "120.00")
    # The talon is charged the day she was queued — and was paid before the CRM.
    assert case["registrar_fee_on"].isoformat() == "2026-03-01"
    assert case["registrar_fee_settled"] is True
    entry = await one(
        owner_engine,
        "SELECT login, created_manually, email_owner, system_id FROM vault_entries "
        "WHERE contact_id = :c",
        c=anna["id"],
    )
    assert entry["login"] == "anna.visa@gmail.com" and entry["created_manually"]
    assert (entry["email_owner"], str(entry["system_id"])) == ("specialist", mail["id"])

    # Both of Наталья's clients, no debt: the old talons are paid.
    ledgers = await ok(await client.get("/api/registrars/ledgers"))
    [natalia] = [r for r in ledgers if r["name"] == "Наталья"]
    assert (natalia["talons"], natalia["balance"], natalia["missing"]) == (0, "0.00", 0)
    oleg = await one(
        owner_engine,
        "SELECT c.registrar_id FROM visa_cases c JOIN contacts p ON p.id = c.contact_id "
        "WHERE p.full_name = 'Сидоров Олег'",
    )
    assert str(oleg["registrar_id"]) == natalia["id"]

    petr = await one(
        owner_engine,
        "SELECT id, birth_date, partner_id, lead_channel_id FROM contacts "
        "WHERE full_name = 'Петров Пётр'",
    )
    assert petr["birth_date"] is None and petr["lead_channel_id"] is None
    assert petr["partner_id"] is not None
    note = await one(
        owner_engine, "SELECT body FROM contact_notes WHERE contact_id = :c", c=petr["id"]
    )
    assert note["body"] == "от Ольги Т."
    greece = await one(
        owner_engine,
        "SELECT c.vac_number, c.registrar_id, f.registrar_fee_amount, f.partner_terms, "
        "f.partner_fixed, f.partner_paid_on FROM visa_cases c "
        "JOIN case_finances f ON f.case_id = c.id WHERE c.contact_id = :c",
        c=petr["id"],
    )
    assert greece["vac_number"] == "AB12" and greece["registrar_id"] is None
    assert greece["registrar_fee_amount"] is None
    assert (greece["partner_terms"], str(greece["partner_fixed"])) == ("fixed", "30.00")
    assert greece["partner_paid_on"] is not None
    assert await count(owner_engine, "waiting_list_entries", tenant_id) == 2  # Anna, Oleg

    # The other year: the same Anna, another case — not the 2026 one overwritten.
    preview = await preview_of(client, "cases", "Учёт.xlsx", book, sheet="Подача25")
    assert (
        next(c for c in preview["columns"] if c["header"] == "Оплата моя посредникам")["field"]
        == "registrar_fee"
    )
    report = await ok(
        await client.post(API, json=body("cases", "Учёт.xlsx", book, preview, sheet="Подача25"))
    )
    assert report["created"] == {"cases": 1, "finances": 1, "payments": 1}
    cases = await rows(
        owner_engine,
        "SELECT c.submission_on, f.registrar_fee_amount, f.registrar_fee_settled "
        "FROM visa_cases c JOIN case_finances f ON f.case_id = c.id "
        "WHERE c.contact_id = :c ORDER BY c.submission_on",
        c=anna["id"],
    )
    assert [(str(c["submission_on"]), str(c["registrar_fee_amount"])) for c in cases] == [
        ("2025-07-01", "100.00"),
        ("2026-06-20", "120.00"),
    ]
    assert all(c["registrar_fee_settled"] for c in cases)

    # Both sheets again: nothing changes.
    for sheet in ("Подача26", "Подача25"):
        preview = await preview_of(client, "cases", "Учёт.xlsx", book, sheet=sheet)
        again = await ok(
            await client.post(
                API,
                json=body("cases", "Учёт.xlsx", book, preview, sheet=sheet)
                | {"values": preview["values"]},
            )
        )
        assert (again["created"], again["updated"]) == ({}, {}), sheet
    assert await count(owner_engine, "visa_cases", tenant_id) == 5
    assert await count(owner_engine, "contacts", tenant_id) == 4

    # A talon from the table can still be counted in the settlements, by hand.
    finance = await ok(
        await client.patch(
            f"/api/cases/{case['id']}/finance", json={"registrar_fee_settled": False}
        )
    )
    assert finance["registrar_fee_settled"] is False
    ledger = await ok(await client.get(f"/api/registrars/{natalia['id']}/ledger"))
    assert (ledger["talons"], ledger["balance"]) == (1, "-120.00")


CONSULTATIONS_2025 = [
    "",
    "ФИО",
    "Дата",
    "email",
    "телефон",
    "Телеграм",
    "Месяц обращения",
    "Делаем визу",
    "Кол-во заявителей",
    "Оплата",
    "Кейс",
]


async def test_consultations_with_a_month_instead_of_a_day(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    long_story = "Хочет в Испанию — " + "подробности " * 600
    content = xlsx_file(
        {
            "Консультации25": [
                CONSULTATIONS_2025,
                [
                    1,
                    "Козлова Мария",
                    "06.04.0205",
                    None,
                    "+375 29 333-33-33",
                    "@maria_k",
                    "Апрель",
                    "да",
                    2,
                    0,
                    long_story,
                ],
                [
                    2,
                    "Козлова Мария (повторно)",
                    datetime(2025, 9, 10),
                    None,
                    None,
                    None,
                    "Сентябрь",
                    "думает",
                    1,
                    50,
                    "Снова про Испанию",
                ],
                [3, None, None, None, None, None, None, None, None, 0, None],
                [None, "ВЫРУЧКА", None, None, None, None, None, None, None, 50, None],
            ],  # fmt: skip
        }
    )
    preview = await preview_of(client, "consultations", "Учёт.xlsx", content)
    fields = [c["field"] for c in preview["columns"]]
    assert fields == [
        None,
        "full_name",
        "held_on",
        "email",
        "phone",
        "telegram",
        "held_month",
        "decision",
        "applicants",
        "fee_amount",
        "consultation_notes",
    ]
    report = await ok(
        await client.post(API, json=body("consultations", "Учёт.xlsx", content, preview))
    )
    assert report["rows"] == 3 and report["imported"] == 2
    assert report["skipped"] == [{"row": 5, "reason": "no_date", "column": None}]
    assert {(w["row"], w["column"], w["reason"]) for w in report["warnings"]} == {
        (2, "Дата", "bad_date"),
        (2, "Месяц обращения", "date_from_month"),
        (2, "Кейс", "truncated"),
    }
    assert await count(owner_engine, "contacts", tenant_id) == 1
    held = await rows(
        owner_engine,
        "SELECT held_on, decision, char_length(notes) AS notes FROM consultations ORDER BY held_on",
    )
    assert [(str(h["held_on"]), h["decision"], h["notes"]) for h in held] == [
        ("2025-04-01", "yes", 5000),
        ("2025-09-10", "thinking", 17),
    ]
    assert await count(owner_engine, "contact_notes", tenant_id) == 0  # «повторно» is no note


POLAND = [
    "",
    "Регистратор",
    "Дата постановки в очередь",
    "Присвоенный номер",
    "Город",
    "Дата записи",
    "Дата подачи",
    "Стоимость в злотых",
    "Стоимость в рублях",
    "Оплата",
    "Остаток",
    "Мой расходник",
    "Комментарии",
    "Почта",
    "Пароль",
    "Почта переадресации",
]


async def test_polish_queue_saved_as_text(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await system(client, "email", "Почта")
    # She has set up Minsk for Poland (no English name), not Grodno.
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    [poland] = [
        r
        for r in (await client.get("/api/reference/rules")).json()
        if r["destination_id"] == countries["PL"]
    ]
    await client.post(
        f"/api/reference/rules/{poland['id']}/visa-centers", json={"cities": ["минск"]}
    )
    # Dates as Excel's days, numbers with «.0»: how a sheet saved as text looks.
    content = tsv_file(
        [
            POLAND,
            ["Иванова Анна", "Наталья", "46082.0", "PLB1234567890", "Минск", "46162.0",
             "46181.0", "100.0", "400.0", "400.0", "0.0", "120.0", "", "anna.pl@mail.ru",
             "PlPass1", "fwd@mail.ru FwdPass1 https://mail.example/fwd"],
            ["Новикова Ольга", "я помогала", "46091.0", "-", "Гродно", "", "", "", "", "", "",
             "0.0", "Удалена из очереди 12.08.2026", "", "", ""],
            ["Белова Ирина", "самостоятельно", "46096.0", "irina@mail.ru", "", "", "", "", "",
             "", "", "", "верификация 12.06", "", "", ""],
            ["", "", "", "", "", "", "", "0.0", "0.0", "0.0", "0.0", "0.0", "", "", "", ""],
        ]
    )  # fmt: skip
    name = "Учёт - Польша с 08.06.tsv"
    preview = await preview_of(client, "queue", name, content)
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    assert preview["options"]["country_id"] == countries["PL"]
    fields = {c["header"]: c["field"] for c in preview["columns"]}
    assert fields[""] is None  # no header: the specialist picks the field once
    assert fields["Дата записи"] == "letter_on" and fields["Дата подачи"] == "submission_on"
    assert fields["Присвоенный номер"] == "vac_number"
    assert fields["Мой расходник"] == "registrar_fee" and fields["Комментарии"] == "queue_note"
    assert (fields["Почта"], fields["Пароль"]) == ("email_login", "email_password")
    samples = {c["header"]: c["samples"] for c in preview["columns"]}
    assert samples["Пароль"] == ["••••••"]
    assert samples["Почта переадресации"] == ["fwd@mail.ru ••••••"]

    request = body("queue", name, content, preview)
    request["columns"]["0"] = "full_name"
    report = await ok(await client.post(API, json=request))
    assert report["rows"] == 3 and report["imported"] == 3
    assert report["created"]["registrar"] == 1  # «я помогала», «самостоятельно» are no one
    # A city she has not set up for Poland is not kept.
    assert {(w["row"], w["column"], w["reason"]) for w in report["warnings"]} == {
        (3, "Город", "unknown_value"),
        (4, "Присвоенный номер", "email_not_number"),
    }
    anna = await one(
        owner_engine,
        "SELECT c.id, c.vac_number, c.submission_on, q.queued_on, q.letter_on, v.city_ru, "
        "f.registrar_fee_amount, f.registrar_fee_settled FROM visa_cases c "
        "JOIN contacts p ON p.id = c.contact_id JOIN waiting_list_entries q ON q.case_id = c.id "
        "JOIN visa_centers v ON v.id = c.visa_center_id "
        "JOIN case_finances f ON f.case_id = c.id WHERE p.full_name = 'Иванова Анна'",
    )
    assert anna["vac_number"] == "PLB1234567890" and anna["city_ru"] == "минск"
    assert [str(anna[k]) for k in ("queued_on", "letter_on", "submission_on")] == [
        "2026-03-01",
        "2026-05-20",
        "2026-06-08",
    ]
    assert str(anna["registrar_fee_amount"]) == "120.00" and anna["registrar_fee_settled"]
    olga = await one(
        owner_engine,
        "SELECT c.vac_number, c.registrar_id, q.removed_on, q.removed_reason "
        "FROM visa_cases c JOIN contacts p ON p.id = c.contact_id "
        "JOIN waiting_list_entries q ON q.case_id = c.id WHERE p.full_name = 'Новикова Ольга'",
    )
    assert (olga["vac_number"], olga["registrar_id"]) == (None, None)
    assert str(olga["removed_on"]) == "2026-08-12"
    assert olga["removed_reason"] == "Удалена из очереди 12.08.2026"
    note = await one(
        owner_engine,
        "SELECT n.body FROM contact_notes n JOIN contacts p ON p.id = n.contact_id "
        "WHERE p.full_name = 'Белова Ирина'",
    )
    assert note["body"] == "верификация 12.06"

    # The column without a header is remembered by its place.
    preview = await preview_of(client, "queue", name, content)
    assert preview["columns"][0]["field"] == "full_name"
    again = await ok(await client.post(API, json=body("queue", name, content, preview)))
    assert (again["created"], again["updated"]) == ({}, {})
    assert await count(owner_engine, "waiting_list_entries", tenant_id) == 3


FRANCE = [
    "",
    "Дата отправки заявки",
    "Пароли",
    "Почта",
    "60й день",
    "Повторно",
    "60й день (2-й цикл)",
    "Повторно (2-й цикл)",
]


async def test_france_sent_again_and_stopped(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    await make_user("anna@example.com")
    await login(client, "anna@example.com")
    await system(client, "email", "Почта")
    d = datetime
    content = xlsx_file(
        {
            "Список Франция ЖО": [
                FRANCE,
                [
                    "Морозов Денис",
                    d(2026, 5, 1),
                    "p1 p2",
                    "denis@mail.ru",
                    d(2026, 6, 29),
                    d(2026, 7, 1),
                    d(2026, 8, 29),
                    "не ставим",
                ],
                ["Зайцева Алла", d(2026, 9, 1), None, None, None, None, None, None],
            ],  # fmt: skip
        }
    )
    preview = await preview_of(client, "attempts", "Учёт.xlsx", content)
    fields = [c["field"] for c in preview["columns"]]
    assert fields == [
        None,
        "sent_on",
        "email_password",
        "email_login",
        "deadline_on",
        "sent_on_2",
        "deadline_on_2",
        "sent_on_3",
    ]
    request = body("attempts", "Учёт.xlsx", content, preview)
    request["columns"]["0"] = "full_name"
    report = await ok(await client.post(API, json=request))
    assert report["created"]["attempts"] == 3
    denis = await rows(
        owner_engine,
        "SELECT a.number, a.sent_on, a.deadline_on, a.outcome, a.closed_on "
        "FROM application_attempts a JOIN visa_cases c ON c.id = a.case_id "
        "JOIN contacts p ON p.id = c.contact_id WHERE p.full_name = 'Морозов Денис' "
        "ORDER BY a.number",
    )
    assert [
        (a["number"], str(a["sent_on"]), str(a["deadline_on"]), a["outcome"], str(a["closed_on"]))
        for a in denis
    ] == [
        (1, "2026-05-01", "2026-06-29", "expired", "2026-06-29"),
        (2, "2026-07-01", "2026-08-29", "cancelled", "2026-08-29"),
    ]
    alla = await one(
        owner_engine,
        "SELECT a.number, a.outcome, a.deadline_on - a.sent_on AS days "
        "FROM application_attempts a JOIN visa_cases c ON c.id = a.case_id "
        "JOIN contacts p ON p.id = c.contact_id WHERE p.full_name = 'Зайцева Алла'",
    )
    assert (alla["number"], alla["outcome"], alla["days"]) == (1, None, 60)
    again = await ok(await client.post(API, json=request))
    assert (again["created"], again["updated"]) == ({}, {})


async def test_italy_new_visa_center_and_bookings(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    site = await system(client, "visa_center", "VFS")
    d = datetime
    italy = xlsx_file(
        {
            "Список Италия ЖО26": [
                [
                    "Столбец 1",
                    "ФИО",
                    "номер",
                    "Дата отправки заявки",
                    "Акция",
                    "постановка в очередь",
                    "Дата постановки в очередь в новом ВЦ",
                    "Город",
                    "Регистратор",
                    "Дата получения письма с датой подачи",
                    "Дата подачи",
                    "Новый ВЦ - данные кабинета",
                    "Дата создания кабинета в новом ВЦ",
                    "Комментарий",
                ],
                [
                    1,
                    "Кузнецова Вера",
                    "vera@mail.ru URN: ITA123456789012",
                    d(2026, 1, 10),
                    d(2026, 1, 10),
                    "август 2025",
                    d(2026, 5, 5),
                    "Минск",
                    "Агентство",
                    d(2026, 7, 1),
                    d(2026, 7, 20),
                    "ITA123456789012 CabPass1",
                    d(2026, 5, 5),
                    "удалили из вейта 15.08.2026",
                ],
            ],  # fmt: skip
        }
    )
    preview = await preview_of(client, "queue", "Учёт.xlsx", italy)
    countries = {c["code"]: c["id"] for c in (await client.get("/api/reference/countries")).json()}
    assert preview["options"]["country_id"] == countries["IT"]
    assert preview["options"]["site_system_id"] == site["id"]
    fields = {c["header"]: c["field"] for c in preview["columns"]}
    assert fields["Дата постановки в очередь в новом ВЦ"] == "requeued_on"
    assert fields["Новый ВЦ - данные кабинета"] == "site_login"
    assert fields["Дата получения письма с датой подачи"] == "letter_on"
    cabinet = next(c for c in preview["columns"] if c["header"] == "Новый ВЦ - данные кабинета")
    assert cabinet["samples"] == ["••••••"]
    report = await ok(await client.post(API, json=body("queue", "Учёт.xlsx", italy, preview)))
    assert report["created"]["queue_entries"] == 2 and report["created"]["passwords"] == 1
    entries = await rows(
        owner_engine,
        "SELECT q.queued_on, q.letter_on, q.removed_on, q.removed_reason, c.vac_number "
        "FROM waiting_list_entries q JOIN visa_cases c ON c.id = q.case_id ORDER BY q.queued_on",
    )
    assert [
        (str(e["queued_on"]), str(e["letter_on"]), str(e["removed_on"]), e["removed_reason"])
        for e in entries
    ] == [
        ("2025-08-01", "None", "2026-05-05", None),
        ("2026-05-05", "2026-07-01", "2026-08-15", "удалили из вейта 15.08.2026"),
    ]
    assert entries[0]["vac_number"] == "ITA123456789012"
    account = await one(owner_engine, "SELECT id, login FROM vault_entries")
    assert account["login"] == "ITA123456789012"
    revealed = await ok(await client.post(f"/api/vault/entries/{account['id']}/reveal"))
    assert revealed["password"] == "CabPass1"

    # Bookings: «до …» dates, two people in one cell — the first one's case.
    await ok(
        await client.post(
            API,
            json=body(
                "cases",
                "w.csv",
                csv_file([["ФИО", "Страна"], ["Петров Олег", "Италия"]]),
            )
            | {"columns": {"0": "full_name", "1": "destination"}},
        )
    )
    bookings = csv_file(
        [
            ["Столбец 1", "Столбец 2", "Столбец 3", "отменить бронирование до", "почта букинга"],
            ["Кузнецова Вера и Петров Олег", "Италия", "Туризм", "до 25.12.2026", "b1@mail.ru"],
            ["Петров Олег", "Италия", "", "до 17.12 2026", ""],
        ]
    )
    preview = await preview_of(client, "bookings", "b.csv", bookings)
    assert [c["field"] for c in preview["columns"]] == [
        None,
        None,
        None,
        "free_cancel_until",
        "booking_account",
    ]
    request = body("bookings", "b.csv", bookings, preview)
    request["columns"] |= {"0": "full_name", "1": "destination"}
    report = await ok(await client.post(API, json=request))
    assert report["created"] == {"bookings": 2}
    assert {(w["row"], w["reason"]) for w in report["warnings"]} == {
        (2, "several_people"),
        (2, "unknown_value"),  # the Booking account is not in the vault yet
    }
    booked = await rows(
        owner_engine,
        "SELECT p.full_name, b.free_cancel_until FROM bookings b "
        "JOIN visa_cases c ON c.id = b.case_id JOIN contacts p ON p.id = c.contact_id "
        "ORDER BY b.free_cancel_until",
    )
    assert [(b["full_name"], str(b["free_cancel_until"])) for b in booked] == [
        ("Петров Олег", "2026-12-17"),
        ("Кузнецова Вера", "2026-12-25"),
    ]
    assert await count(owner_engine, "contacts", tenant_id) == 2

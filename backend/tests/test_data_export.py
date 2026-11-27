"""Export of a workspace in the VisaCRM data format (backlog 1.21, docs/data-format.md →
«Выгрузка»): every sheet with the CRM's codes, a file the import reads back. Loaded back
as it is, it changes nothing and adds nothing; an edit made in the file comes back, a row
changed in the CRM since the export does not. Codes of another workspace find nothing.
Only the owner exports; passwords only after her account's password and, with two-factor
login on, a code from the app. The log keeps the counts, never a client's data."""

import base64
import io
import uuid
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import unquote

import openpyxl
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_system
from app.core.demo import DemoSettings, seed, specialist_email
from tests.support.helpers import (
    CLOCK,
    PASSWORD,
    TOTP_SECRET,
    MakeUser,
    import_report,
    login,
    workspace_today,
)
from tests.support.pilot_book_visacrm import SYSTEMS
from tests.support.pilot_book_visacrm import xlsx as pilot_xlsx
from tests.test_data_import import BOOK, CASES, CLIENTS, mail_system, rows, run

EXPORT = "/api/imports/export"
DEMO_PASSWORD = "demo-password-42"
SHEETS = [
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
# A workspace's records an export holds.
TABLES = (
    "contacts",
    "contact_notes",
    "consultations",
    "visa_cases",
    "submission_groups",
    "case_finances",
    "case_payments",
    "case_expenses",
    "waiting_list_entries",
    "application_attempts",
    "bookings",
    "vault_entries",
)


async def ok(response: Response) -> Any:
    assert response.status_code in (200, 201), response.text
    return response.json()


async def exported(client: AsyncClient, **body: Any) -> Response:
    """An export confirmed with the account's password, as every export is."""
    response = await client.post(EXPORT, json={"password": PASSWORD, **body})
    assert response.status_code == 200, response.text
    return response


def file_name(response: Response) -> str:
    """The name the browser saves the download under (RFC 6266, in UTF-8)."""
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    return unquote(disposition.rpartition("UTF-8''")[2])


def opened(content: bytes) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(content))


def table(book: openpyxl.Workbook, sheet: str) -> list[dict[str, Any]]:
    """A sheet's rows by header."""
    values = list(book[sheet].iter_rows(values_only=True))
    return [dict(zip(values[0], row, strict=True)) for row in values[1:]]


def edit(book: openpyxl.Workbook, sheet: str, code: str, header: str, value: Any) -> None:
    """Change one cell of the row with the code, as the specialist would in Excel."""
    rows_ = book[sheet]
    headers = [c.value for c in rows_[1]]
    for row in rows_.iter_rows(min_row=2):
        if row[0].value == code:
            row[headers.index(header)].value = value
            return
    raise AssertionError(f"no row {code} on {sheet}")


def saved(book: openpyxl.Workbook) -> bytes:
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


async def load_back(client: AsyncClient, content: bytes, *, save: bool = True) -> dict:
    body = {"file_name": "VisaCRM-выгрузка.xlsx", "content": base64.b64encode(content).decode()}
    return await import_report(client, body, save=save)


async def counts(engine: AsyncEngine, tenant: uuid.UUID) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            name: await conn.scalar(
                text(f"SELECT count(*) FROM {name} WHERE tenant_id = :t"), {"t": tenant}
            )
            for name in TABLES
        }


async def ids(engine: AsyncEngine, name: str, tenant: uuid.UUID) -> set[str]:
    async with engine.connect() as conn:
        found = await conn.scalars(
            text(f"SELECT id::text FROM {name} WHERE tenant_id = :t"), {"t": tenant}
        )
        return set(found)


async def events(engine: AsyncEngine, tenant: uuid.UUID) -> list[dict]:
    return await rows(
        engine,
        "SELECT action, details::text AS details FROM audit_events WHERE tenant_id = :t "
        "AND action IN ('imports.exported', 'vault.passwords_exported') ORDER BY id",
        t=tenant,
    )


async def seed_demo(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        scope_to_system(session)
        await seed(session, DemoSettings(DEMO_PASSWORD, TOTP_SECRET, specialists=2, clients=25))
        await session.commit()


async def tenant_of(engine: AsyncEngine, email: str) -> uuid.UUID:
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT tenant_id FROM users WHERE email = :e"), {"e": email})


async def test_an_export_of_a_workspace_loads_back_unchanged_and_without_duplicates(
    client: AsyncClient,
    owner_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
):
    # The made-up workspaces of the demo stand: clients with every kind of record.
    await seed_demo(sessionmaker)
    tenant = await tenant_of(owner_engine, specialist_email(1))
    assert (await login(client, specialist_email(1), DEMO_PASSWORD)).status_code == 200
    before = await counts(owner_engine, tenant)
    assert all(before.values()), before

    for language in ("ru", "en"):
        content = (await exported(client, language=language, password=DEMO_PASSWORD)).content
        book = opened(content)
        if language == "ru":
            assert book.sheetnames == SHEETS
            # A row's code is the CRM's id of its record.
            codes = {row["Код клиента"] for row in table(book, "Клиенты")}
            assert codes == await ids(owner_engine, "contacts", tenant)

        report = await load_back(client, content)

        assert report["skipped"] == [], report["skipped"]
        assert report["warnings"] == [], report["warnings"]
        assert (report["created"], report["updated"]) == ({}, {}), language
        assert report["imported"] == report["rows"] > 0
        assert await counts(owner_engine, tenant) == before


async def test_an_edit_in_the_file_comes_back_but_a_row_changed_in_the_crm_since_does_not(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)
    book = opened((await exported(client)).content)
    cases = {row["Клиент"]: row for row in table(book, "Кейсы")}
    anna, olga = cases["Петрова Анна Сергеевна"], cases["Сидоренко Ольга"]
    [first] = [p for p in table(book, "Платежи") if p["Сумма"] == 200]

    # In the file: Anna's case and payment, and Olga's case...
    edit(book, "Кейсы", anna["Код кейса"], "Номер в ВЦ", "PL777")
    edit(book, "Платежи", first["Код"], "Сумма", 250)
    edit(book, "Кейсы", olga["Код кейса"], "Номер в ВЦ", "FR-file")
    # ...which is changed in the CRM after the export.
    await ok(await client.patch(f"/api/cases/{olga['Код кейса']}", json={"vac_number": "FR1"}))

    report = await load_back(client, saved(book))

    assert report["skipped"] == [
        {"sheet": "Кейсы", "row": 4, "column": None, "reason": "changed_after_export"}
    ]
    assert report["created"] == {}
    assert report["updated"] == {"cases": 1, "payments": 1}
    numbers = await rows(
        owner_engine,
        "SELECT vac_number FROM visa_cases WHERE tenant_id = :t AND vac_number IS NOT NULL "
        "ORDER BY number",
        t=tenant,
    )
    assert [n["vac_number"] for n in numbers] == ["PL777", "FR1"]
    [payment] = await rows(
        owner_engine, "SELECT amount FROM case_payments WHERE id = :p", p=first["Код"]
    )
    assert payment["amount"] == 250


async def test_an_export_of_another_workspace_finds_none_of_its_records(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    other = await make_user("other@example.com")
    await login(client, "other@example.com")
    await mail_system(client)
    await run(client, BOOK)
    theirs = (await exported(client)).content
    their_counts = await counts(owner_engine, other)
    their_cases = await ids(owner_engine, "visa_cases", other)
    client.cookies.clear()
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)

    # Her own export holds nothing of theirs.
    mine = opened((await exported(client)).content)
    assert all(table(mine, sheet) == [] for sheet in SHEETS[:10])

    # Their file loaded here: their codes find no record, the rows make her own ones.
    report = await load_back(client, theirs)

    assert report["skipped"] == [], report["skipped"]
    assert report["updated"] == {}
    assert report["created"]["contacts"] == 3 and report["created"]["cases"] == 3
    assert await counts(owner_engine, other) == their_counts
    assert not their_cases & await ids(owner_engine, "visa_cases", tenant)
    [case] = await rows(
        owner_engine,
        "SELECT count(*) AS n FROM visa_cases WHERE tenant_id = :t AND vac_number = 'PL123'",
        t=other,
    )
    assert case["n"] == 1


async def test_passwords_are_exported_only_with_the_password_and_the_code(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    # A specialist of the tests has two-factor login on.
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)
    secrets = ("Pa55word", "BkPass1")

    # Without passwords: the column is there and empty.
    plain = await exported(client)
    book = opened(plain.content)
    assert [row["Пароль"] for row in table(book, "Учётные записи")] == [None, None]
    assert ["Пароли", "без паролей"] in [[c.value for c in r] for r in book["О файле"].rows]
    day = workspace_today().isoformat()
    assert file_name(plain) == f"VisaCRM-выгрузка-{day}.xlsx"
    assert plain.headers["cache-control"] == "no-store"

    # No password, a wrong one, no code, a wrong code or a recovery code: no file.
    assert (await client.post(EXPORT, json={"with_passwords": True})).status_code == 422
    for password, code, error in (
        ("wrong-password", CLOCK.next_code(), "invalid_current_password"),
        (PASSWORD, None, "invalid_code"),
        (PASSWORD, "000000", "invalid_code"),
        (PASSWORD, "ABCD-EFGH-IJKL", "invalid_code"),
    ):
        response = await client.post(
            EXPORT, json={"with_passwords": True, "password": password, "code": code}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == error
    assert len(await events(owner_engine, tenant)) == 1

    with_passwords = await exported(
        client, with_passwords=True, password=PASSWORD, code=CLOCK.next_code()
    )
    book = opened(with_passwords.content)
    assert sorted(row["Пароль"] for row in table(book, "Учётные записи")) == sorted(secrets)
    assert ["Пароли", "с паролями"] in [[c.value for c in r] for r in book["О файле"].rows]
    assert file_name(with_passwords) == f"VisaCRM-выгрузка-с-паролями-{day}.xlsx"
    assert with_passwords.headers["cache-control"] == "no-store"

    # One event per export with the counts; the passwords shown, how many.
    logged = await events(owner_engine, tenant)
    assert [e["action"] for e in logged] == [
        "imports.exported",
        "imports.exported",
        "vault.passwords_exported",
    ]
    assert '"count": 2' in logged[2]["details"]
    assert '"clients": 3' in logged[0]["details"]
    for event in logged:
        for value in (*secrets, "Петрова", "+375291112233", "anna.p@example.com"):
            assert value not in event["details"]

    # The file with passwords loads back as it is: the passwords are the vault's.
    report = await load_back(client, with_passwords.content)
    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])


async def test_without_two_factor_login_the_password_alone_confirms_the_passwords(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com", two_factor=False)
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)

    wrong = await client.post(EXPORT, json={"with_passwords": True, "password": "nope"})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "invalid_current_password"
    response = await exported(client, with_passwords=True, password=PASSWORD)
    passwords = [row["Пароль"] for row in table(opened(response.content), "Учётные записи")]
    assert sorted(passwords) == ["BkPass1", "Pa55word"]


async def test_every_export_asks_for_the_account_password(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The owner's decision on PR #162: the file holds every client's data, so a session
    alone does not take it — the account's password does; the code from the app is
    asked only for the vault's passwords. A wrong password counts as a failed login."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")

    missing = await client.post(EXPORT, json={})
    assert missing.status_code == 422, missing.text
    wrong = await client.post(EXPORT, json={"password": "wrong-password"})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "invalid_current_password"
    assert await events(owner_engine, tenant) == []
    [user] = await rows(
        owner_engine,
        "SELECT failed_login_count FROM users WHERE email = 'spec@example.com'",
    )
    assert user["failed_login_count"] == 1

    # The right password is enough without passwords, two-factor login on or not.
    await exported(client)
    assert [e["action"] for e in await events(owner_engine, tenant)] == ["imports.exported"]


async def test_only_the_owner_exports(client: AsyncClient, make_user: MakeUser):
    tenant = await make_user("spec@example.com")
    await make_user("emp@example.com", role="employee", tenant_id=tenant)
    await login(client, "emp@example.com")

    for body in ({"password": PASSWORD}, {"with_passwords": True, "password": PASSWORD}):
        response = await client.post(EXPORT, json=body)
        assert response.status_code == 403, response.text


async def test_sheets_and_columns_of_a_module_the_workspace_has_not_are_left_out(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {name: BOOK[name] for name in ("Клиенты", "Кейсы", "Платежи")})
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET modules = array_remove(array_remove(modules, 'finances'), "
                "'vault') WHERE id = :t"
            ),
            {"t": tenant},
        )

    book = opened((await exported(client)).content)

    assert "Платежи" not in book.sheetnames and "Учётные записи" not in book.sheetnames
    headers = [c.value for c in book["Кейсы"][1]]
    assert "Номер в ВЦ" in headers and "Стоимость" not in headers and "Талон" not in headers
    report = await load_back(client, saved(book))
    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])
    assert report["warnings"] == []


async def test_an_account_of_a_switched_off_system_loads_back(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await mail_system(client)
    await run(client, BOOK)
    [booking] = [
        s
        for s in await ok(await client.get("/api/dictionaries/vault-systems"))
        if s["name"] == "Booking.com"
    ]
    await ok(
        await client.patch(
            f"/api/dictionaries/vault-systems/{booking['id']}", json={"is_active": False}
        )
    )

    report = await load_back(client, (await exported(client)).content)

    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])


async def test_a_case_opened_late_in_the_evening_keeps_its_day_and_time(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """«Дата открытия» is the day in the specialist's time zone: 22:30 UTC on 10 March is
    already 11 March in Minsk. Loaded back, the day is the case's own: its time stays."""
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")
    await run(client, {name: BOOK[name] for name in ("Клиенты", "Кейсы")})
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE visa_cases SET created_at = '2025-03-10 22:30:00+00' "
                "WHERE tenant_id = :t AND vac_number = 'PL123'"
            ),
            {"t": tenant},
        )

    content = (await exported(client)).content
    [anna] = [r for r in table(opened(content), "Кейсы") if r["Номер в ВЦ"] == "PL123"]
    assert anna["Дата открытия"].date() == date(2025, 3, 11)
    report = await load_back(client, content)

    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])
    [case] = await rows(
        owner_engine,
        "SELECT created_at FROM visa_cases WHERE tenant_id = :t AND vac_number = 'PL123'",
        t=tenant,
    )
    assert case["created_at"] == datetime(2025, 3, 10, 22, 30, tzinfo=UTC)


async def test_the_day_a_case_opened_holds_west_of_utc_too(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Midnight UTC is still the day before in New York: a file's «Дата открытия» there is
    the specialist's own midnight, so the same file loaded again changes nothing."""
    tenant = await make_user("spec@example.com")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET timezone = 'America/New_York' WHERE id = :t"), {"t": tenant}
        )
    await login(client, "spec@example.com")
    cases = [[*CASES[0], "Дата открытия"], *([*row, date(2025, 1, 10)] for row in CASES[1:])]
    await run(client, {"Клиенты": CLIENTS, "Кейсы": cases})

    again = await run(client, {"Клиенты": CLIENTS, "Кейсы": cases})

    assert (again["created"], again["updated"], again["skipped"]) == ({}, {}, [])
    content = (await exported(client)).content
    assert {r["Дата открытия"].date() for r in table(opened(content), "Кейсы")} == {
        date(2025, 1, 10)
    }
    report = await load_back(client, content)
    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])


async def test_a_passport_typed_in_the_card_loads_back_unchanged(
    client: AsyncClient, make_user: MakeUser
):
    """The card keeps the passport page whole, its empty fields too (sex: null): an export's
    empty «Пол» is that same empty field, not a change."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    passport = {"passport_number": "AB1234567", "nationality": "BY"}
    await ok(
        await client.post(
            "/api/contacts",
            json={"last_name": "Петрова", "first_name": "Анна", "passport": passport},
        )
    )

    report = await load_back(client, (await exported(client)).content)

    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])


async def test_the_pilot_book_goes_through_load_export_load(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """docs/pilot.md: the made-up book of the first specialist, loaded, exported and loaded
    back — the file to check the CRM against before the old tables are let go."""
    tenant = await make_user("pilot@example.com")
    await login(client, "pilot@example.com")
    for name, kind in SYSTEMS:
        await ok(
            await client.post("/api/dictionaries/vault-systems", json={"name": name, "kind": kind})
        )
    first = await import_report(
        client,
        {"file_name": "VisaCRM-импорт.xlsx", "content": base64.b64encode(pilot_xlsx()).decode()},
    )
    assert first["skipped"] == []
    before = await counts(owner_engine, tenant)

    report = await load_back(client, (await exported(client)).content)

    assert (report["created"], report["updated"], report["skipped"]) == ({}, {}, [])
    assert await counts(owner_engine, tenant) == before


async def test_the_old_column_mappings_are_gone(owner_engine: AsyncEngine):
    """The table of the import with column mapping (1.14, 1.16) went with its code."""
    async with owner_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('import_mappings')")) is None

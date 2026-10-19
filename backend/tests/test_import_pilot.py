"""Перенос таблиц перед пилотом (backlog 1.15, docs/pilot.md): the whole book of the first
specialist, made up after the owner's description, imported sheet by sheet in the order
the guide gives. One person becomes one card across the sheets, a queue or France finds
the case of «Подача», old talons are no debt, and the second run changes nothing and
needs no column set by hand."""

import base64
from datetime import date

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login, workspace_today
from tests.pilot_book import PLAN, Step, xlsx
from tests.test_import_sheets import rows, system
from tests.test_imports import API, count, ok

FILE = "Общий список клиентов.xlsx"


async def run(client: AsyncClient, content: str, step: Step, values: dict) -> tuple[dict, dict]:
    """One sheet as the specialist imports it: open it, set what the CRM did not guess,
    import. Returns the preview and the report."""
    request = {"kind": step.kind, "file_name": FILE, "content": content, "sheet": step.sheet}
    preview = await ok(await client.post(f"{API}/preview", json=request))
    columns = {str(c["index"]): c["field"] for c in preview["columns"] if c["field"]}
    for column in preview["columns"]:
        if column["header"] in step.by_hand and str(column["index"]) not in columns:
            columns[str(column["index"])] = step.by_hand[column["header"]]
    options = {k: v for k, v in preview["options"].items() if v is not None}
    if step.country:
        countries = (await client.get("/api/reference/countries")).json()
        options["country_id"] = next(c["id"] for c in countries if c["code"] == step.country)
    choices = {**preview["values"], **values.get(step.sheet, {})}
    report = await ok(
        await client.post(
            API, json=request | {"columns": columns, "options": options, "values": choices}
        )
    )
    return preview, report


async def test_the_whole_book_in_the_guide_order(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
) -> None:
    tenant_id = await make_user("anna@example.com")
    await login(client, "anna@example.com")
    content = base64.b64encode(xlsx()).decode()
    # What a new workspace starts with (app/core/dictionaries.py), as far as the book needs.
    await system(client, "email", "Почта")
    await system(client, "visa_center", "VFS Global")
    channels = {}
    for name in ("Instagram", "Сарафан"):
        channels[name] = (
            await ok(await client.post("/api/dictionaries/channels", json={"name": name}))
        )["id"]
    # The specialist's spellings: her channels are the list's ones; «PK doc» is a partner.
    values: dict[str, dict] = {
        "2024 Подача": {"channel": {"инста": channels["Instagram"]}},
        "Подача25": {"channel": {"сарафанка": channels["Сарафан"]}},
        "Подача26": {"channel": {"pk doc": "partner"}},
    }

    reports: dict[str, dict] = {}
    for step in PLAN:
        if step.sheet == "Подача26":
            # The registrar the 2025 sheet added: «Наталья» and «Наталья В.» are her.
            registrars = await ok(await client.get("/api/dictionaries/registrars"))
            natasha = next(r["id"] for r in registrars if r["name"] == "Наташа")
            values["Подача26"]["registrar"] = {"наталья": natasha, "наталья в": "as:Наталья"}
        reports[step.sheet] = (await run(client, content, step, values))[1]

    # Every row is in, but the totals and «ВЫРУЧКА»; placeholder rows are not counted.
    assert {s: (r["rows"], r["imported"]) for s, r in reports.items()} == {
        "2024 Консультации": (1, 1),
        "Консультации25": (2, 2),
        "Консультации26": (7, 6),
        "2023": (1, 1),
        "2024 Подача": (2, 1),
        "Подача25": (2, 2),
        "Подача26": (7, 6),
        "Польша с 08.06": (2, 2),
        "Латвия с 17.08": (1, 1),
        "Список Италия ЖО26": (1, 1),
        "Список ЖО25": (1, 1),
        "Список Франция ЖО": (1, 1),
        "В работе": (2, 2),
    }
    # One card per person, whichever sheets they are in.
    # Sorted here: the database's collation orders «Сидоров О…» and «Сидорова И…» either way.
    people = await rows(owner_engine, "SELECT full_name FROM contacts")
    assert sorted(p["full_name"] for p in people) == [
        "Зайцева Алла",
        "Иванова Анна",
        "Козлова Мария",
        "Кузнецова Вера",
        "Морозов Денис",
        "Новикова Ольга",
        "Орлова Нина + супруг + сын",  # several people in one cell: question 19
        "Петров Пётр",
        "Сидоров Олег",
        "Сидорова Ирина",
    ]

    # Anna: two years, two cases, each from the consultation before it; the Polish queue
    # and her talon are the case's of 2026, counted once.
    anna = await rows(
        owner_engine,
        "SELECT c.number, c.submission_on, c.vac_number, k.held_on, f.registrar_fee_amount, "
        "f.registrar_fee_settled, (SELECT count(*) FROM waiting_list_entries q "
        "WHERE q.case_id = c.id) AS entries FROM visa_cases c "
        "JOIN contacts p ON p.id = c.contact_id LEFT JOIN consultations k "
        "ON k.id = c.consultation_id LEFT JOIN case_finances f ON f.case_id = c.id "
        "WHERE p.full_name = 'Иванова Анна' ORDER BY c.number",
    )
    assert [
        (str(a["held_on"]), a["vac_number"], str(a["registrar_fee_amount"]), a["entries"])
        for a in anna
    ] == [
        ("2025-03-10", None, "100.00", 0),
        ("2026-01-15", "PLB1234567890", "120.00", 1),
    ]
    assert all(a["registrar_fee_settled"] for a in anna)
    # Case numbers go by the years, oldest first.
    numbers = await rows(
        owner_engine,
        "SELECT c.number, coalesce(c.submission_on, c.created_at::date) AS day "
        "FROM visa_cases c ORDER BY c.number",
    )
    assert numbers[0]["day"].year == 2023 and numbers[1]["day"].year == 2024

    # Registrars: «Наташа» (her three spellings; the choice made in «Подача» holds in the
    # queues too) and the agency — no debt to either: the talons from the book are paid.
    ledgers = await ok(await client.get("/api/registrars/ledgers"))
    assert {(r["name"], r["balance"], r["talons"], r["missing"]) for r in ledgers} == {
        ("Наташа", "0.00", 0, 0),
        ("Агентство Виза", "0.00", 0, 0),
    }
    # Italy: the live queue, then the new visa center; France: sent again, then stopped.
    vera = await rows(
        owner_engine,
        "SELECT q.queued_on, q.removed_on FROM waiting_list_entries q "
        "JOIN visa_cases c ON c.id = q.case_id JOIN contacts p ON p.id = c.contact_id "
        "WHERE p.full_name = 'Кузнецова Вера' ORDER BY q.queued_on",
    )
    assert [(str(q["queued_on"]), str(q["removed_on"])) for q in vera] == [
        ("2025-08-01", "2026-05-05"),
        ("2026-05-05", "None"),
    ]
    denis = await rows(
        owner_engine,
        "SELECT a.outcome FROM application_attempts a ORDER BY a.number",
    )
    assert [a["outcome"] for a in denis] == ["expired", "cancelled"]
    olga = await rows(owner_engine, "SELECT queued_on FROM waiting_list_entries q "
                      "JOIN visa_cases c ON c.id = q.case_id JOIN contacts p "
                      "ON p.id = c.contact_id WHERE p.full_name = 'Новикова Ольга'")  # fmt: skip
    assert olga[0]["queued_on"] == date(workspace_today().year, 8, 17)
    booked = await rows(
        owner_engine,
        "SELECT p.full_name, c.number FROM bookings b JOIN visa_cases c ON c.id = b.case_id "
        "JOIN contacts p ON p.id = c.contact_id",
    )
    booked = sorted(booked, key=lambda b: b["full_name"])
    assert [b["full_name"] for b in booked] == ["Иванова Анна", "Морозов Денис"]
    assert booked[0]["number"] == anna[1]["number"]  # the open case, not the 2025 one

    # The book again, sheet by sheet: no column to set by hand, nothing new, nothing changed.
    counts = {
        table: await count(owner_engine, table, tenant_id)
        for table in ("contacts", "visa_cases", "waiting_list_entries", "vault_entries")
    }
    for step in PLAN:
        again = Step(step.sheet, step.kind, country=step.country)
        preview, report = await run(client, content, again, {})
        assert all(c["field"] for c in preview["columns"] if c["header"] in step.by_hand), (
            step.sheet
        )
        assert (report["created"], report["updated"]) == ({}, {}), step.sheet
    assert counts == {table: await count(owner_engine, table, tenant_id) for table in counts}

"""Перенос таблиц перед пилотом (backlog 1.15, 1.20; docs/pilot.md): the made-up book of the
first specialist (tests/pilot_book.py), turned into the VisaCRM data format by following the
prompt (tests/pilot_book_visacrm.py), loads in one go. One person is one card across the
years, the queues and France find the case of «Подача», old talons are no debt, a case of
2023 stays in 2023, and loading the file again changes nothing."""

import base64

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import MakeUser, login
from tests.pilot_book_visacrm import SYSTEMS, xlsx

API = "/api/imports"
TABLES = {
    "contacts": 10,
    "contact_notes": 7,
    "consultations": 9,
    "visa_cases": 11,
    "case_payments": 9,
    "waiting_list_entries": 7,
    "application_attempts": 2,
    "vault_entries": 8,
    "bookings": 2,
}


def content() -> str:
    return base64.b64encode(xlsx()).decode()


async def ok(response) -> dict | list:
    assert response.status_code in (200, 201), response.text
    return response.json()


async def rows(engine: AsyncEngine, sql: str) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql))).mappings()]


async def test_the_pilot_book_in_the_format_loads_whole_and_once(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    tenant = await make_user("pilot@example.com")
    await login(client, "pilot@example.com")
    for name, kind in SYSTEMS:
        await ok(
            await client.post("/api/dictionaries/vault-systems", json={"name": name, "kind": kind})
        )
    request = {"file_name": "VisaCRM-импорт.xlsx", "content": content()}

    report = await ok(await client.post(API, json=request))

    assert report["skipped"] == []
    # The test workspace has no visa centers in Belarus: the cities are not found.
    assert {(w["sheet"], w["column"], w["reason"]) for w in report["warnings"]} <= {
        ("Кейсы", "Город подачи", "unknown_value")
    }
    for table, number in TABLES.items():
        async with owner_engine.connect() as conn:
            found = await conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant}
            )
        assert found == number, table
    assert report["created"]["passwords"] == 8

    # Anna: two visas to Poland, from the consultation before each.
    anna = await rows(
        owner_engine,
        "SELECT k.held_on, c.submission_on, c.outcome FROM visa_cases c "
        "JOIN contacts p ON p.id = c.contact_id JOIN consultations k ON k.id = c.consultation_id "
        "WHERE p.full_name = 'Иванова Анна' ORDER BY k.held_on",
    )
    assert [(str(a["held_on"]), a["outcome"]) for a in anna] == [
        ("2025-03-10", "visa"),
        ("2026-01-15", None),
    ]
    # The case of 2023 without a single date stays in 2023; Oleg's «думает» became «yes».
    [old] = await rows(owner_engine, "SELECT created_at FROM visa_cases WHERE vac_number = 'CD34'")
    assert old["created_at"].year == 2023
    [oleg] = await rows(
        owner_engine,
        "SELECT k.decision FROM consultations k JOIN contacts p ON p.id = k.contact_id "
        "WHERE p.full_name = 'Сидоров Олег'",
    )
    assert oleg["decision"] == "yes"
    # Italy: the first queue ends where the new visa center's starts.
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
    # The talons from the book are paid: no debt to the registrars.
    ledgers = await ok(await client.get("/api/registrars/ledgers"))
    assert {(r["name"], r["balance"], r["talons"]) for r in ledgers} == {
        ("Наталья", "0.00", 0),
        ("Агентство Виза", "0.00", 0),
    }

    again = await ok(await client.post(API, json=request))
    assert (again["created"], again["updated"], again["skipped"]) == ({}, {}, [])

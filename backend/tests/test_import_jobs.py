"""The import runs in the background (backlog 1.21, docs/data-format.md → «Проверка и
загрузка»): the request only starts it, the page asks how far it is. A request held open
for minutes is cut by the hosting's proxy or by a phone going to sleep, and the specialist
saw «нет связи с сервером». The import asks the National Bank nothing: a payment without a
rate of its own takes the site's latest rate (app/core/rates.py)."""

import asyncio
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import rates
from app.core.data_import import WorkbookImport
from tests.helpers import MakeUser, finished_import, login
from tests.test_data_import import body

API = "/api/imports"
CLIENTS = [
    ["Код клиента", "Фамилия", "Имя", "Телефон"],
    ["K-1", "Петрова", "Анна", "+375291112233"],
]
CASES = [
    ["Код кейса", "Код клиента", "Клиент", "Страна визы", "Стоимость"],
    ["C-1", "K-1", "Петрова Анна", "Польша", 800],
]
PAYMENT_HEADER = ["Код кейса", "Клиент", "Дата", "Сумма", "Валюта", "Курс"]


def payments(first: date, count: int) -> list[list]:
    """Payments of 100 euro on `count` days in a row, from `first`."""
    return [PAYMENT_HEADER] + [
        ["C-1", "Петрова Анна", first + timedelta(days=i), 100, "EUR", None] for i in range(count)
    ]


def book(first: date, count: int) -> dict[str, list[list]]:
    return {"Клиенты": CLIENTS, "Кейсы": CASES, "Платежи": payments(first, count)}


@pytest.fixture
async def held_import(monkeypatch: pytest.MonkeyPatch):
    """A run waits before its first row until the test lets it go: it keeps running."""
    gate = asyncio.Event()
    load = WorkbookImport.load

    async def held(self) -> None:
        await gate.wait()
        await load(self)

    monkeypatch.setattr(WorkbookImport, "load", held)
    yield gate
    gate.set()


async def test_a_check_answers_at_once_and_runs_in_the_background(
    client: AsyncClient, make_user: MakeUser, held_import: asyncio.Event
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    started = await client.post(f"{API}/check", json=body(book(date(2025, 3, 1), 3)))

    assert started.status_code == 202
    job = started.json()
    assert (job["kind"], job["status"], job["file_name"]) == (
        "check",
        "running",
        "VisaCRM-импорт.xlsx",
    )
    assert job["report"] is None
    [running] = (await client.get(f"{API}/jobs")).json()
    assert running["id"] == job["id"] and running["status"] == "running"

    held_import.set()
    done = await finished_import(client, started)
    assert done["status"] == "done" and done["error"] is None
    assert done["report"]["imported"] == 5 and done["report"]["id"] is None
    assert (done["rows_done"], done["rows_total"]) == (5, 5)
    assert (await client.get(f"{API}/jobs")).json() == []


async def test_the_import_asks_the_bank_nothing_and_takes_the_sites_latest_rate(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = await make_user("spec@example.com")
    await login(client, "spec@example.com")

    def bank(currency: str, day: date) -> Decimal:
        raise AssertionError("the import asked the National Bank")

    monkeypatch.setattr(rates, "_fetch", bank)
    sheets = book(date(2024, 3, 1), 20)
    # A payment with its own rate keeps it; a talon in euro takes the latest rate too.
    sheets["Платежи"].append(["C-1", "Петрова Анна", date(2025, 6, 1), 100, "EUR", 3.5])

    done = await finished_import(client, await client.post(API, json=body(sheets)))

    assert done["status"] == "done", done
    assert done["report"]["created"]["payments"] == 21 and done["report"]["skipped"] == []
    async with owner_engine.connect() as conn:
        paid = (
            await conn.execute(
                text(
                    "SELECT rate, amount_base, count(*) AS n FROM case_payments "
                    "WHERE tenant_id = :t GROUP BY rate, amount_base ORDER BY rate"
                ),
                {"t": tenant},
            )
        ).all()
    assert [(str(r.rate), str(r.amount_base), r.n) for r in paid] == [
        ("3.400000", "340.00", 20),
        ("3.500000", "350.00", 1),
    ]


async def test_one_import_at_a_time_and_a_new_check_replaces_a_running_one(
    client: AsyncClient, make_user: MakeUser, held_import: asyncio.Event
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    first = await client.post(API, json=body(book(date(2025, 1, 1), 2)))
    assert first.status_code == 202
    for path in (f"{API}/check", API):
        busy = await client.post(path, json=body(book(date(2025, 2, 1), 2)))
        assert busy.status_code == 409 and busy.json()["detail"] == "import_running"
    held_import.set()
    assert (await finished_import(client, first))["status"] == "done"

    held_import.clear()
    older = await client.post(f"{API}/check", json=body(book(date(2025, 3, 1), 2)))
    newer = await client.post(f"{API}/check", json=body(book(date(2025, 3, 1), 2)))
    assert newer.status_code == 202
    held_import.set()
    assert (await finished_import(client, older))["status"] == "stopped"
    assert (await finished_import(client, newer))["status"] == "done"


async def test_the_job_of_another_workspace_is_not_found(client: AsyncClient, make_user: MakeUser):
    await make_user("spec@example.com")
    await make_user("other@example.com")
    await login(client, "spec@example.com")
    job = await finished_import(
        client, await client.post(f"{API}/check", json=body({"Клиенты": CLIENTS}))
    )

    client.cookies.clear()
    await login(client, "other@example.com")
    foreign = await client.get(f"{API}/jobs/{job['id']}")
    assert foreign.status_code == 404 and foreign.json()["detail"] == "import_job_not_found"
    # A job the server no longer knows (it restarted): the page says the run broke off.
    lost = await client.get(f"{API}/jobs/{uuid.uuid4()}")
    assert lost.status_code == 404 and lost.json()["detail"] == "import_job_not_found"


async def test_a_failed_run_says_so_and_logs_no_client_data(
    client: AsyncClient,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    async def broken(self, workbook):
        raise RuntimeError("Петрова Анна +375291112233")

    monkeypatch.setattr(WorkbookImport, "run", broken)
    # The migrations of the test database run in this process, and Alembic's logging
    # config switches off the loggers made before it.
    monkeypatch.setattr(logging.getLogger("app.core.import_jobs"), "disabled", False)
    with caplog.at_level(logging.ERROR):
        job = await finished_import(client, await client.post(API, json=body({"Клиенты": CLIENTS})))

    assert (job["status"], job["error"], job["report"]) == ("failed", "import_failed", None)
    assert "RuntimeError" in caplog.text and "Петрова" not in caplog.text
    assert (await client.get(API)).json() == []

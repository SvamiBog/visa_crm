"""The import runs in the background (backlog 1.21, docs/data-format.md → «Проверка и
загрузка»): the request only starts it, the page asks how far it is. A request held open
for minutes is cut by the hosting's proxy or by a phone going to sleep, and the specialist
saw «нет связи с сервером». The National Bank rates the file needs are asked for ahead, a
few at a time, not one by one per row."""

import logging
import threading
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

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
    """Payments in euro on `count` days in a row, from `first`: a rate for each day."""
    return [PAYMENT_HEADER] + [
        ["C-1", "Петрова Анна", first + timedelta(days=i), 100, "EUR", None] for i in range(count)
    ]


def book(first: date, count: int) -> dict[str, list[list]]:
    return {"Клиенты": CLIENTS, "Кейсы": CASES, "Платежи": payments(first, count)}


@pytest.fixture
def held_bank(monkeypatch: pytest.MonkeyPatch):
    """The bank answers only once the test lets it: a job keeps running meanwhile."""
    gate = threading.Event()

    def fetch(currency: str, day: date) -> Decimal:
        if not gate.wait(10):
            raise rates.RateUnavailable(currency)
        return Decimal("3.400000")

    monkeypatch.setattr(rates, "_fetch", fetch)
    yield gate
    gate.set()


async def test_a_check_answers_at_once_and_runs_in_the_background(
    client: AsyncClient, make_user: MakeUser, held_bank: threading.Event
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    started = await client.post(f"{API}/check", json=body(book(date(2031, 1, 1), 3)))

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

    held_bank.set()
    done = await finished_import(client, started)
    assert done["status"] == "done" and done["error"] is None
    assert done["report"]["imported"] == 5 and done["report"]["id"] is None
    assert (done["rates_done"], done["rates_total"]) == (3, 3)
    assert (done["rows_done"], done["rows_total"]) == (5, 5)
    assert (await client.get(f"{API}/jobs")).json() == []


async def test_the_rates_of_the_whole_file_are_asked_ahead_a_few_at_a_time(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    lock = threading.Lock()
    calls: list[tuple[str, date]] = []
    now = {"asking": 0, "most": 0}

    def slow(currency: str, day: date) -> Decimal:
        with lock:
            calls.append((currency, day))
            now["asking"] += 1
            now["most"] = max(now["most"], now["asking"])
        time.sleep(0.05)
        with lock:
            now["asking"] -= 1
        return Decimal("3.400000")

    monkeypatch.setattr(rates, "_fetch", slow)
    sheets = book(date(2031, 3, 1), 20)
    # A payment with its own rate and one in the base currency ask the bank nothing.
    sheets["Платежи"] += [
        ["C-1", "Петрова Анна", date(2031, 6, 1), 50, "EUR", 3.5],
        ["C-1", "Петрова Анна", date(2031, 6, 2), 50, "BYN", None],
    ]

    done = await finished_import(client, await client.post(API, json=body(sheets)))

    assert done["status"] == "done" and done["kind"] == "import"
    assert done["report"]["created"]["payments"] == 22 and done["report"]["skipped"] == []
    assert sorted(calls) == [("EUR", date(2031, 3, 1) + timedelta(days=i)) for i in range(20)]
    assert 1 < now["most"] <= rates.PREFETCH_CONCURRENCY
    # Kept for the next run: the import after a check asks the bank nothing.
    again = await finished_import(client, await client.post(f"{API}/check", json=body(sheets)))
    assert again["status"] == "done" and len(calls) == 20


async def test_one_import_at_a_time_and_a_new_check_replaces_a_running_one(
    client: AsyncClient, make_user: MakeUser, held_bank: threading.Event
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    first = await client.post(API, json=body(book(date(2032, 1, 1), 2)))
    assert first.status_code == 202
    for path in (f"{API}/check", API):
        busy = await client.post(path, json=body(book(date(2032, 2, 1), 2)))
        assert busy.status_code == 409 and busy.json()["detail"] == "import_running"
    held_bank.set()
    assert (await finished_import(client, first))["status"] == "done"

    held_bank.clear()
    older = await client.post(f"{API}/check", json=body(book(date(2032, 3, 1), 2)))
    newer = await client.post(f"{API}/check", json=body(book(date(2032, 3, 1), 2)))
    assert newer.status_code == 202
    held_bank.set()
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

"""The import keeps going where a file is a little off (backlog 1.21, 1.22): a value the
database would refuse is passed by with a line of the report instead of losing its row or
the whole run, a file another program wrote is read whole, a broken one is refused as
unreadable, and a check that was replaced leaves nothing behind."""

import asyncio
import base64
import io
import re
import zipfile
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import imports as imports_api
from app.core.data_import import WorkbookImport
from app.core.workbook import WorkbookError, read_workbook
from tests.helpers import MakeUser, finished_import, login, uploaded
from tests.test_data_import import body, check, rows, run
from tests.test_workbook_rules import book, reasons

API = "/api/imports"
CLIENTS = [
    ["Код клиента", "Фамилия", "Имя", "Телефон"],
    ["K-1", "Петрова", "Анна", "+375291112233"],
    ["K-2", "Петров", "Игорь", "+375297778899"],
]


def lines(report: dict, kind: str) -> list[tuple]:
    return [(i["reason"], i["sheet"], i["row"], i["column"]) for i in report[kind]]


def rewritten(data: bytes, name: str, change) -> bytes:
    """The XLSX with one of its parts changed."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            part = source.read(info.filename)
            target.writestr(info, change(part) if info.filename == name else part)
    return out.getvalue()


# --- The reader -------------------------------------------------------------------------


def test_a_sheet_whose_declared_size_is_wrong_is_read_whole():
    """Some programs write «A1» as the sheet's size whatever is on it."""
    data = book(
        {
            "Клиенты": [
                ["Код клиента", "Фамилия", "Имя"],
                ["K-1", "Петрова", "Анна"],
                ["K-2", "Петров", "Игорь"],
            ]
        }
    )
    data = rewritten(
        data,
        "xl/worksheets/sheet1.xml",
        lambda xml: re.sub(rb"<dimension [^>]*/>", b'<dimension ref="A1"/>', xml),
    )

    result = read_workbook(data)

    assert [row.values for row in result.sheets["clients"].rows] == [
        {"code": "K-1", "last_name": "Петрова", "first_name": "Анна"},
        {"code": "K-2", "last_name": "Петров", "first_name": "Игорь"},
    ]


def test_a_file_with_a_broken_sheet_is_unreadable():
    data = book({"Клиенты": [["Код клиента", "Имя"], ["K-1", "Анна"]]})
    data = rewritten(data, "xl/worksheets/sheet1.xml", lambda xml: xml[: len(xml) // 2])

    with pytest.raises(WorkbookError) as error:
        read_workbook(data)

    assert error.value.code == "file_unreadable"


def test_formulas_without_a_saved_result_are_reported():
    """A file written by a script keeps a formula without its result: the cell reads empty,
    and the report says why."""
    result = read_workbook(
        book(
            {
                "Клиенты": [["Код клиента", "Имя"], ["K-1", "Анна"]],
                "Кейсы": [
                    ["Код кейса", "Код клиента", "Страна визы", "Стоимость"],
                    ["C-1", "K-1", "Польша", "=500+100"],
                ],
            }
        )
    )

    assert reasons(result.warnings) == [("formula", "Кейсы", 2, "Стоимость")]
    assert "price" not in result.sheets["cases"].rows[0].values


# --- Rows the database would refuse -----------------------------------------------------


async def test_a_free_consultation_is_loaded_without_a_fee(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await run(
        client,
        {
            "Клиенты": CLIENTS,
            "Консультации": [
                ["Код консультации", "Код клиента", "Дата", "Стоимость", "Валюта", "Оплачено"],
                ["Q-1", "K-1", date(2025, 1, 15), 0, "BYN", "нет"],
                ["Q-2", "K-2", date(2025, 1, 16), None, "EUR", None],
            ],
        },
    )

    assert report["skipped"] == []
    assert report["created"]["consultations"] == 2
    fees = await rows(owner_engine, "SELECT fee_amount, fee_currency FROM consultations")
    assert fees == [{"fee_amount": None, "fee_currency": None}] * 2


async def test_days_of_stay_without_a_visa_are_passed_by(client: AsyncClient, make_user: MakeUser):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")

    report = await run(
        client,
        {
            "Клиенты": CLIENTS,
            "Кейсы": [
                ["Код кейса", "Код клиента", "Семья", "Страна визы", "Дней пребывания"],
                ["C-1", "K-1", "Петровы", "Польша", 90],
                ["C-2", "K-2", "Петровы", "Польша", None],
            ],
        },
    )

    assert report["skipped"] == []
    assert lines(report, "warnings") == [("needs_outcome", "Кейсы", 2, "Дней пребывания")]
    assert report["created"]["cases"] == 2 and report["created"]["groups"] == 1


async def test_a_refused_case_of_a_family_does_not_fail_the_file(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """The database refuses the first case; the family's other cases still load."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    finance = WorkbookImport._finance

    async def refused(self, r, case):
        if r.values["code"] == "C-1":
            raise IntegrityError("refused", {}, Exception())
        return await finance(self, r, case)

    monkeypatch.setattr(WorkbookImport, "_finance", refused)

    report = await check(
        client,
        {
            "Клиенты": [*CLIENTS, ["K-3", "Петрова", "Мария", None]],
            "Кейсы": [
                ["Код кейса", "Код клиента", "Семья", "Страна визы"],
                ["C-1", "K-1", "Петровы", "Польша"],
                ["C-2", "K-2", "Петровы", "Польша"],
                ["C-3", "K-3", "Петровы", "Польша"],
            ],
        },
    )

    assert lines(report, "skipped") == [("row_error", "Кейсы", 2, None)]
    assert report["created"]["cases"] == 2 and report["created"]["groups"] == 1


# --- Runs -------------------------------------------------------------------------------


async def test_a_replaced_check_leaves_no_cards(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
):
    """A check that was replaced while it made its cards does not hand them to the file:
    they are of the old choices."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    gates = [asyncio.Event(), asyncio.Event()]
    waiting = iter(gates)
    making = asyncio.Event()
    cards = imports_api.cards

    async def held(engine):
        making.set()
        await next(waiting).wait()
        return await cards(engine)

    monkeypatch.setattr(imports_api, "cards", held)
    upload = await uploaded(client, body({"Клиенты": CLIENTS}))
    path = f"{API}/uploads/{upload['id']}"

    first = await client.post(f"{path}/check", json={})
    await asyncio.wait_for(making.wait(), 30)
    second = await client.post(f"{path}/check", json={})
    gates[0].set()
    job = await finished_import(client, first)

    assert job["status"] == "stopped"
    assert (await client.get(f"{API}/uploads/current")).json()["checked"] is False
    confirmed = await client.post(f"{path}/import", json={})
    assert (confirmed.status_code, confirmed.json()["detail"]) == (409, "import_not_checked")

    gates[1].set()
    assert (await finished_import(client, second))["status"] == "done"
    assert (await client.get(f"{API}/uploads/current")).json()["checked"] is True


async def test_a_broken_file_is_refused_on_upload(client: AsyncClient, make_user: MakeUser):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    data = book({"Клиенты": [["Код клиента", "Имя"], ["K-1", "Анна"]]})
    data = rewritten(data, "xl/worksheets/sheet1.xml", lambda xml: xml[: len(xml) // 2])

    response = await client.post(
        f"{API}/uploads",
        json={"file_name": "broken.xlsx", "content": base64.b64encode(data).decode()},
    )

    assert (response.status_code, response.json()["detail"]) == (422, "file_unreadable")


# --- Matching -----------------------------------------------------------------------------

CASES = [
    ["Код кейса", "Код клиента", "Страна визы"],
    ["C-1", "K-1", "Польша"],
]


async def test_equal_payments_of_one_day_are_each_kept(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Two instalments of the same sum on one day are two payments, in a repeated file too."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    day = date(2025, 3, 1)
    book_ = {
        "Клиенты": CLIENTS,
        "Кейсы": CASES,
        "Платежи": [
            ["Код кейса", "Дата", "Сумма"],
            ["C-1", day, 100],
            ["C-1", day, 100],
        ],
        "Расходы": [
            ["Код кейса", "Дата", "Описание", "Сумма"],
            ["C-1", day, "Курьер", 20],
            ["C-1", day, "Курьер", 20],
        ],
    }

    first = await run(client, book_)
    again = await run(client, book_)
    book_["Платежи"].append(["C-1", day, 100])
    third = await run(client, book_)

    assert (first["created"]["payments"], first["created"]["expenses"]) == (2, 2)
    assert again["created"] == {} and again["updated"] == {}
    assert third["created"] == {"payments": 1}
    sums = await rows(owner_engine, "SELECT sum(amount) AS paid FROM case_payments")
    assert sums == [{"paid": 300}]


async def test_a_phone_shared_by_a_family_does_not_merge_its_people(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """The row of a person with another given name is not the phone's card: the person
    gets a card of their own, without the phone, and the report says so."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book_ = {
        "Клиенты": [
            ["Код клиента", "Фамилия", "Имя", "Телефон", "Email"],
            ["K-1", "Петрова", "Анна", "+375291112233", None],
            ["K-2", "Петрова", "Мария", "+375291112233", "maria@example.com"],
        ]
    }

    first = await run(client, book_)
    again = await run(client, book_)

    assert first["created"] == {"contacts": 2}
    assert lines(first, "warnings") == [("phone_taken", "Клиенты", 3, "Телефон")]
    assert again["created"] == {} and again["updated"] == {}
    contacts = await rows(
        owner_engine, "SELECT full_name, phone, email FROM contacts ORDER BY full_name"
    )
    assert contacts == [
        {"full_name": "Петрова Анна", "phone": "+375291112233", "email": None},
        {"full_name": "Петрова Мария", "phone": None, "email": "maria@example.com"},
    ]


async def test_a_telegram_shared_by_a_family_does_not_merge_its_people(
    client: AsyncClient, make_user: MakeUser, owner_engine: AsyncEngine
):
    """Owner's decision 2026-11-18: a family shares the Telegram account as it shares the
    phone, so the nick is checked the same way. A child with the parent's phone and nick
    gets a card of their own instead of going into the parent's, and a repeated file
    finds both."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    book_ = {
        "Клиенты": [
            ["Код клиента", "Фамилия", "Имя", "Телефон", "Telegram", "Дата рождения"],
            ["K-1", "Петрова", "Анна", "+375291112233", "@petrova_fam", date(1988, 3, 12)],
            ["K-2", "Петров", "Миша", "+375291112233", "@petrova_fam", date(2015, 7, 5)],
        ]
    }

    first = await run(client, book_)
    again = await run(client, book_)

    assert first["created"] == {"contacts": 2}
    assert lines(first, "warnings") == [("phone_taken", "Клиенты", 3, "Телефон")]
    assert again["created"] == {} and again["updated"] == {}
    contacts = await rows(
        owner_engine, "SELECT full_name, phone, telegram FROM contacts ORDER BY birth_date"
    )
    assert contacts == [
        {"full_name": "Петрова Анна", "phone": "+375291112233", "telegram": "petrova_fam"},
        {"full_name": "Петров Миша", "phone": None, "telegram": "petrova_fam"},
    ]


# --- The choices of the check -------------------------------------------------------------


async def test_the_file_is_loaded_with_the_choices_it_was_checked_with(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    sheets = {"Клиенты": [["Код клиента", "Имя", "Канал прихода"], ["K-1", "Анна", "Сарафан"]]}
    upload = await uploaded(client, body(sheets))
    path = f"{API}/uploads/{upload['id']}"
    skip = {"choices": {"channel": {"сарафан": "skip"}}}
    assert (await finished_import(client, await client.post(f"{path}/check", json=skip)))[
        "status"
    ] == "done"

    # The page opened again gets the choices back.
    assert (await client.get(f"{API}/uploads/current")).json()["choices"] == skip["choices"]
    other = await client.post(f"{path}/import", json={})
    assert (other.status_code, other.json()["detail"]) == (409, "import_choices_changed")

    job = await finished_import(client, await client.post(f"{path}/import", json=skip))
    assert job["status"] == "done" and job["report"]["created"] == {"contacts": 1}


# --- Refused rows -------------------------------------------------------------------------


async def test_refused_rows_are_undone_without_reading_the_workspace_again(
    client: AsyncClient,
    make_user: MakeUser,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """Rows the database refuses after they wrote are taken back from what the import
    remembers; the rows after them go on as if the refused ones were not in the file."""
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    loads = 0
    load = WorkbookImport.load

    async def counted(self) -> None:
        nonlocal loads
        loads += 1
        await load(self)

    monkeypatch.setattr(WorkbookImport, "load", counted)
    for sheet in ("clients", "consultations", "cases", "payments", "expenses", "bookings"):

        def refusing(handler):
            async def handle(self, r):
                await handler(self, r)
                if str(r.values.get("code", "")).startswith("BAD"):
                    await self.session.flush()
                    raise IntegrityError("refused", {}, Exception())

            return handle

        monkeypatch.setattr(
            WorkbookImport, f"_{sheet}", refusing(getattr(WorkbookImport, f"_{sheet}"))
        )
    day = date(2025, 3, 1)
    await run(client, {"Клиенты": [CLIENTS[0], CLIENTS[1]], "Кейсы": CASES})
    loads = 0

    report = await run(
        client,
        {
            "Клиенты": [
                ["Код клиента", "Фамилия", "Имя", "Телефон", "Email"],
                ["BAD-1", "Петрова", "Анна", "+375291112233", "anna@example.com"],
                ["BAD-2", "Сидоренко", "Ольга", "+375295550011", None],
                ["K-3", "Сидоренко", "Ольга", "+375295550011", None],
                ["K-1", "Петрова", "Анна", "+375291112233", None],
            ],
            "Консультации": [
                ["Код консультации", "Код клиента", "Дата", "Решение"],
                ["BAD-Q", "K-3", day, "думает"],
                ["Q-1", "K-3", day, "думает"],
            ],
            "Кейсы": [
                ["Код кейса", "Код клиента", "Страна визы", "Источник", "Стоимость"],
                ["BAD-C", "K-3", "Польша", "А", 500],
                ["C-3", "K-3", "Польша", "А", 600],
                ["BAD-C1", "K-1", "Польша", None, 700],
            ],
            "Платежи": [
                ["Код", "Код кейса", "Дата", "Сумма"],
                ["BAD-P", "C-3", day, 100],
                ["P-1", "C-3", day, 100],
            ],
            "Расходы": [
                ["Код", "Код кейса", "Дата", "Описание", "Сумма"],
                ["BAD-E", "C-3", day, "Курьер", 20],
                ["E-1", "C-3", day, "Курьер", 20],
            ],
            "Брони": [
                ["Код", "Код кейса", "Место"],
                ["BAD-B", "C-3", "Hotel"],
                ["B-1", "C-3", "Hotel"],
            ],
        },
    )

    # The import: once for its check, once for the load.
    assert loads == 2
    assert report["skipped_count"] == 8
    assert {i["reason"] for i in report["skipped"]} == {"row_error"}
    assert report["created"] == {
        "contacts": 1,
        "consultations": 1,
        "cases": 1,
        "finances": 1,
        "payments": 1,
        "expenses": 1,
        "bookings": 1,
    }
    assert report["updated"] == {}
    contacts = await rows(owner_engine, "SELECT full_name, email FROM contacts ORDER BY full_name")
    assert contacts == [
        {"full_name": "Петрова Анна", "email": None},
        {"full_name": "Сидоренко Ольга", "email": None},
    ]
    cases = await rows(
        owner_engine,
        "SELECT c.number, f.price, k.decision FROM visa_cases c "
        "LEFT JOIN case_finances f ON f.case_id = c.id "
        "LEFT JOIN consultations k ON k.id = c.consultation_id ORDER BY c.number",
    )
    # The refused case took no number, and no price went to the case of an earlier file.
    assert [(c["number"], c["price"], c["decision"]) for c in cases] == [
        (1, None, None),
        (2, 600, "yes"),
    ]

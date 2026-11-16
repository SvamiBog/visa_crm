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

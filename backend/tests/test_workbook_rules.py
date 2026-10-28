"""Reading a file in the VisaCRM data format (backlog 1.20): sheets and headers in either
language, what is passed by, which rows are skipped and why, and the limits of the file."""

import io
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook as XlsxBook

from app.core import workbook
from app.core.workbook import WorkbookError, read_workbook


def book(sheets: dict[str, list[list]]) -> bytes:
    """An XLSX file with the given sheets, first row — headers."""
    xlsx = XlsxBook()
    xlsx.remove(xlsx.active)
    for title, rows in sheets.items():
        sheet = xlsx.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    xlsx.save(out)
    return out.getvalue()


def reasons(issues) -> list[tuple]:
    return [(i.reason, i.sheet, i.row, i.column) for i in issues]


def test_sheets_and_headers_in_russian_or_english_are_recognised():
    result = read_workbook(
        book(
            {
                "Клиенты": [
                    ["Код клиента*", "Фамилия", "Имя", "Телефон"],
                    ["К1", "Петрова", "Анна", "+375291112233"],
                ],
                "Cases": [
                    ["Case code", "Client code", "Visa country", "Submitted on", "Price"],
                    ["C1", "К1", "Польша", date(2025, 5, 20), 600],
                ],
            }
        )
    )
    assert set(result.sheets) == {"clients", "cases"}
    assert result.sheets["clients"].rows[0].values == {
        "code": "К1",
        "last_name": "Петрова",
        "first_name": "Анна",
        "phone": "+375291112233",
    }
    case = result.sheets["cases"].rows[0]
    assert case.number == 2
    assert case.values["submission_on"] == date(2025, 5, 20)
    assert case.values["price"] == Decimal("600.00")
    assert result.sheets["cases"].headers["destination"] == "Visa country"
    assert result.skipped == [] and result.warnings == []


def test_unknown_sheets_and_columns_are_passed_by_with_a_warning():
    result = read_workbook(
        book(
            {
                "Клиенты": [["Код клиента", "Имя", "Прибыль"], ["К1", "Анна", 100]],
                "Отчёт": [["что-то"]],
                "Справочник": [["Страна"], ["Польша"]],
                "О файле": [["Формат", "VisaCRM"]],
            }
        )
    )
    assert reasons(result.warnings) == [
        ("unknown_column", "Клиенты", None, "Прибыль"),
        ("unknown_sheet", "Отчёт", None, None),
    ]
    assert result.sheets["clients"].rows[0].values == {"code": "К1", "first_name": "Анна"}


def test_a_sheet_without_a_required_column_is_not_read():
    result = read_workbook(book({"Кейсы": [["Код кейса", "Код клиента"], ["C1", "К1"]]}))
    assert "cases" not in result.sheets
    assert reasons(result.skipped) == [("missing_column", "Кейсы", None, "Страна визы")]


def test_a_repeated_column_is_read_once():
    result = read_workbook(
        book({"Клиенты": [["Код клиента", "Имя", "Имя"], ["К1", "Анна", "Аня"]]})
    )
    assert result.sheets["clients"].rows[0].values["first_name"] == "Анна"
    assert reasons(result.warnings) == [("duplicate_column", "Клиенты", None, "Имя")]


def test_a_bad_cell_skips_its_row_in_a_required_column_and_only_the_cell_elsewhere():
    result = read_workbook(
        book(
            {
                "Консультации": [
                    ["Код консультации", "Код клиента", "Дата", "Заявителей"],
                    ["k1", "К1", "17.08", 1],
                    ["k2", "К1", "15.01.2025", "два"],
                    ["k3", None, "16.01.2025", 1],
                ]
            }
        )
    )
    rows = result.sheets["consultations"].rows
    assert [r.number for r in rows] == [3]
    assert rows[0].values == {"code": "k2", "client": "К1", "held_on": date(2025, 1, 15)}
    assert reasons(result.skipped) == [
        ("date", "Консультации", 2, "Дата"),
        ("required", "Консультации", 4, "Код клиента"),
    ]
    assert reasons(result.warnings) == [("number", "Консультации", 3, "Заявителей")]


def test_a_code_repeated_on_its_sheet_skips_the_later_row():
    result = read_workbook(
        book({"Клиенты": [["Код клиента", "Имя"], ["К1", "Анна"], ["К1", "Аня"]]})
    )
    assert [r.number for r in result.sheets["clients"].rows] == [2]
    assert reasons(result.skipped) == [("duplicate_code", "Клиенты", 3, "Код клиента")]


def test_empty_cells_of_present_columns_are_remembered_and_empty_rows_passed_by():
    result = read_workbook(
        book(
            {
                "Клиенты": [
                    ["Код клиента", "Имя", "Телефон"],
                    ["К1", "Анна", None],
                    [None, None, None],
                    ["К2", "Олег", "+375297778899"],
                ]
            }
        )
    )
    rows = result.sheets["clients"].rows
    assert [r.number for r in rows] == [2, 4]
    assert rows[0].blank == {"phone"}
    assert rows[1].blank == set()


def test_reference_columns_keep_only_the_client_name():
    result = read_workbook(
        book(
            {
                "Кейсы": [
                    ["Код кейса", "Код клиента", "Клиент", "Номер кейса", "Страна визы", "Остаток"],
                    ["C1", "К1", " Петрова Анна ", 101, "Польша", "много"],
                ]
            }
        )
    )
    row = result.sheets["cases"].rows[0]
    assert row.values == {
        "code": "C1",
        "client": "К1",
        "client_name": "Петрова Анна",
        "destination": "Польша",
    }
    assert result.skipped == [] and result.warnings == []


def test_the_export_time_is_read_from_the_about_sheet():
    result = read_workbook(
        book(
            {
                "О файле": [
                    ["Формат", "VisaCRM, версия 1"],
                    ["Выгружено", datetime(2026, 10, 27, 14, 30)],
                ]
            }
        )
    )
    assert result.exported_at == datetime(2026, 10, 27, 14, 30)
    assert read_workbook(book({"Клиенты": [["Код клиента"]]})).exported_at is None


def test_a_file_over_the_size_limit_or_not_xlsx_is_refused(monkeypatch):
    with pytest.raises(WorkbookError, match="file_unreadable"):
        read_workbook(b"name;phone\n")
    data = book({"Клиенты": [["Код клиента"], ["К1"]]})
    monkeypatch.setattr(workbook, "MAX_BYTES", len(data) - 1)
    with pytest.raises(WorkbookError, match="file_too_large"):
        read_workbook(data)


def test_rows_over_the_limit_on_all_sheets_together_are_refused(monkeypatch):
    monkeypatch.setattr(workbook, "MAX_ROWS", 2)
    data = book(
        {
            "Клиенты": [["Код клиента"], ["К1"], ["К2"]],
            "Заметки": [["Код клиента", "Текст"], ["К1", "звонить вечером"]],
        }
    )
    with pytest.raises(WorkbookError, match="too_many_rows"):
        read_workbook(data)

"""Reading a file in the VisaCRM data format (backlog 1.21): sheets and headers in either
language, what is passed by, which rows are skipped and why, and the limits of the file
(backlog S5: the stylesheet, running out of memory, one load of the book)."""

import io
import re
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from xml.etree.ElementTree import ParseError
from xml.parsers import expat

import openpyxl
import pytest
from lxml import etree
from openpyxl import Workbook as XlsxBook
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

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


def repacked(data: bytes, parts: dict[str, bytes | Callable[[bytes], bytes]]) -> bytes:
    """The XLSX with some of its parts replaced or added (bytes) or changed (a function of
    the part)."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            part, new = source.read(info.filename), parts.get(info.filename)
            target.writestr(info, new(part) if callable(new) else part if new is None else new)
        for name, new in parts.items():
            if name not in source.namelist():
                assert isinstance(new, bytes)
                target.writestr(name, new)
    return out.getvalue()


def stuffed_styles(data: bytes, formats: int) -> bytes:
    """The XLSX with that many more cell formats in its stylesheet: openpyxl builds an object
    for each of them."""
    xf = b'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'

    def stuff(xml: bytes) -> bytes:
        return re.sub(rb'<cellXfs count="(\d+)">', lambda m: m.group(0) + xf * formats, xml)

    return repacked(data, {"xl/styles.xml": stuff})


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


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """openpyxl.load_workbook, counted: whether each load asked for the saved results."""
    calls: list[bool] = []
    load = openpyxl.load_workbook

    def counted(*args, **kwargs):
        calls.append(kwargs.get("data_only", False))
        return load(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", counted)
    return calls


def test_a_stylesheet_over_its_limit_is_refused_before_openpyxl_reads_it(loads: list[bool]):
    """openpyxl reads the stylesheet by its fixed name and builds an object for every cell
    format in it, whatever the sheets hold: a file of a few KB grows to tens of MB."""
    data = stuffed_styles(book({"Клиенты": [["Код клиента"], ["К1"]]}), 40_000)
    assert len(data) < 32 * 1024

    with pytest.raises(WorkbookError) as error:
        read_workbook(data)

    assert error.value.code == "file_too_large"
    assert loads == []


def test_a_stylesheet_up_to_its_limit_is_read(monkeypatch: pytest.MonkeyPatch):
    data = book({"Клиенты": [["Код клиента"], ["К1"]]})
    size = zipfile.ZipFile(io.BytesIO(data)).getinfo("xl/styles.xml").file_size

    monkeypatch.setattr(workbook, "MAX_STYLES", size)
    assert read_workbook(data).sheets["clients"].rows[0].values == {"code": "К1"}

    monkeypatch.setattr(workbook, "MAX_STYLES", size - 1)
    with pytest.raises(WorkbookError, match="file_too_large"):
        read_workbook(data)


def _expat_out_of_memory() -> Exception:
    error = ParseError("out of memory")
    error.code = expat.errors.codes[expat.errors.XML_ERROR_NO_MEMORY]
    return error


# How running out of memory shows: Python's own, and the parse errors of libxml2 (lxml,
# which openpyxl uses for rels) and expat (ElementTree) when they fail to allocate.
OUT_OF_MEMORY = {
    "MemoryError": MemoryError,
    "libxml2": lambda: etree.XMLSyntaxError("Memory allocation failed", 2, 1, 1, None),
    "expat": _expat_out_of_memory,
}


@pytest.mark.parametrize("kind", OUT_OF_MEMORY)
@pytest.mark.parametrize("where", ["loading the book", "reading its rows"])
def test_running_out_of_memory_reads_as_a_file_too_large(
    monkeypatch: pytest.MonkeyPatch, where: str, kind: str
):
    """The parser runs under a memory limit (parse_upload): reaching it is the file being too
    large, not a broken one."""

    def fail(*args, **kwargs):
        raise OUT_OF_MEMORY[kind]()

    def fail_on_first_row(*args, **kwargs):
        raise OUT_OF_MEMORY[kind]()
        yield  # a generator, as openpyxl's: it fails when the first row is asked for

    if where == "loading the book":
        monkeypatch.setattr(openpyxl, "load_workbook", fail)
    else:
        monkeypatch.setattr(ReadOnlyWorksheet, "iter_rows", fail_on_first_row)

    with pytest.raises(WorkbookError) as error:
        read_workbook(book({"Клиенты": [["Код клиента"], ["К1"]]}))

    assert error.value.code == "file_too_large"


def test_the_book_is_loaded_once_with_its_saved_results(loads: list[bool]):
    """Formulas without a result are found in the sheet's XML, not in a second copy of the
    book held in memory beside the first."""
    result = read_workbook(
        book(
            {
                "Кейсы": [
                    ["Код кейса", "Код клиента", "Страна визы", "Стоимость"],
                    ["C-1", "K-1", "Польша", "=500+100"],
                ]
            }
        )
    )

    assert loads == [True]
    assert reasons(result.warnings) == [("formula", "Кейсы", 2, "Стоимость")]


_HEAD = ["Код кейса", "Код клиента", "Страна визы", "Стоимость"]


def _cell(value: str, ref: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def _case_row(number: int, code: str, price: str) -> str:
    """A row of «Кейсы» whose «Стоимость» cell is given as XML."""
    cells = "".join(
        _cell(value, f"{column}{number}")
        for column, value in zip("ABC", (code, "K-1", "Польша"), strict=True)
    )
    return f'<row r="{number}">{cells}{price}</row>'


# Row 2: a formula without its result; row 3 is missing; row 2 again (openpyxl passes by a
# row that goes back); row 4: a shared formula with its result; row 5: the same shared
# formula without one; row 6: a plain number.
_SHEET = (
    '<row r="1">'
    + "".join(_cell(h, f"{c}1") for c, h in zip("ABCD", _HEAD, strict=True))
    + "</row>"
    + _case_row(2, "C-2", '<c r="D2"><f>500+100</f><v></v></c>')
    + _case_row(2, "C-X", '<c r="D2"><f>1+1</f></c>')
    + _case_row(4, "C-4", '<c r="D4"><f t="shared" ref="D4:D5" si="0">1+1</f><v>2</v></c>')
    + _case_row(5, "C-5", '<c r="D5"><f t="shared" si="0"/></c>')
    + _case_row(6, "C-6", '<c r="D6"><v>300</v></c>')
)


def _without_cell_refs(xml: str) -> str:
    return re.sub(r'<c r="[A-Z]+\d+"', "<c", xml)


def _without_any_refs(xml: str) -> str:
    """Rows numbered by their order: the one that went back becomes row 3, and so on."""
    return re.sub(r'<row r="\d+"', "<row", _without_cell_refs(xml))


@pytest.mark.parametrize(
    ("sheet", "rows", "unsaved"),
    [
        (_SHEET, [2, 4, 5, 6], [2, 5]),
        (_without_cell_refs(_SHEET), [2, 4, 5, 6], [2, 5]),
        (_without_any_refs(_SHEET), [2, 3, 4, 5, 6], [2, 3, 5]),
    ],
    ids=["references", "no cell references", "no references"],
)
def test_formulas_are_found_in_the_cells_openpyxl_reads(sheet: str, rows: list, unsaved: list):
    """The sheet's XML is read beside openpyxl and must number rows and cells as it does: a
    row or a cell without its reference follows the one before, a row that goes back is
    passed by. A formula with its result is read as the result."""
    xml = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{sheet}</sheetData></worksheet>"
    ).encode()
    data = repacked(book({"Кейсы": [["x"]]}), {"xl/worksheets/sheet1.xml": xml})

    result = read_workbook(data)

    read = result.sheets["cases"].rows
    assert [row.number for row in read] == rows
    assert reasons(result.warnings) == [("formula", "Кейсы", n, "Стоимость") for n in unsaved]
    prices = {row.number: row.values.get("price") for row in read}
    assert prices[4] == Decimal("2.00") and prices[6] == Decimal("300.00")

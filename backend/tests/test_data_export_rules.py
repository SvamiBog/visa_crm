"""How the export writes a record's values (backlog 1.21, docs/data-format.md → «Выгрузка»):
the format's words in the interface's language, real dates and numbers, text as it is —
never a formula — and «О файле» with the time the import compares records with."""

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl

from app.core.data_export import Lists, cell_value, workbook
from app.core.data_format import BY_KEY
from app.core.data_records import Localized
from app.core.workbook import read_workbook

CASES = BY_KEY["cases"]
NOTES = BY_KEY["notes"]
CLIENTS = BY_KEY["clients"]


def test_values_are_the_format_words_in_the_language():
    outcome = CASES.column("outcome")
    assert cell_value(outcome, "visa", "ru") == "виза"
    assert cell_value(outcome, "visa", "en") == "visa"
    appeal = CASES.column("after_appeal")
    assert (cell_value(appeal, True, "ru"), cell_value(appeal, False, "en")) == ("да", "no")
    country = CASES.column("destination")
    assert cell_value(country, Localized("Польша", "Poland"), "en") == "Poland"
    destinations = BY_KEY["consultations"].column("destinations")
    both = [Localized("Польша", "Poland"), Localized("Латвия", "Latvia")]
    assert cell_value(destinations, both, "ru") == "Польша, Латвия"
    assert cell_value(destinations, [], "ru") is None


def test_dates_and_sums_are_real_cells():
    # The passport's dates are kept as ISO text on the card's questionnaire.
    issued = CLIENTS.column("passport_issued_on")
    assert cell_value(issued, "2020-02-29", "ru") == date(2020, 2, 29)
    price = CASES.column("price")
    assert cell_value(price, Decimal("800.50"), "ru") == Decimal("800.50")


def test_text_stays_text_and_loses_only_what_a_file_cannot_hold():
    body = NOTES.column("body")
    assert cell_value(body, '=HYPERLINK("x")', "ru") == '=HYPERLINK("x")'
    notes = [
        {"code": "n1", "client": "c1", "body": "=1+1"},
        {"code": "n2", "client": "c1", "body": "строка\x0bвторая"},
    ]

    # Control codes pasted into a note, a list's name or the workspace's name are dropped:
    # XML cannot hold them, and one would break the whole file.
    content = workbook(
        Lists(channels=["Insta\x01gram"]),
        language="ru",
        workspace="Кабинет\x02",
        base_currency="BYN",
        sheets=[(NOTES, NOTES.columns), (CLIENTS, CLIENTS.columns)],
        rows={"notes": notes},
    )

    sheet = openpyxl.load_workbook(io.BytesIO(content))["Заметки"]
    cell = sheet.cell(2, 5)
    assert (cell.value, cell.data_type) == ("=1+1", "s")
    book = read_workbook(content)
    assert [r.values["body"] for r in book.sheets["notes"].rows] == ["=1+1", "строкавторая"]
    lists = openpyxl.load_workbook(io.BytesIO(content))["Справочник"]
    assert "Instagram" in [c.value for column in lists.iter_cols() for c in column]


def test_about_says_when_the_file_was_exported_and_with_what():
    at = datetime(2026, 11, 24, 21, 59, 59, 987000)
    content = workbook(
        Lists(),
        language="en",
        workspace="Workspace",
        base_currency="BYN",
        exported_at=at,
        with_passwords=False,
    )

    about = [[c.value for c in row] for row in openpyxl.load_workbook(io.BytesIO(content))["About"]]
    assert ["Passwords", "without passwords"] in about
    # The import reads the time back to the millisecond.
    assert read_workbook(content).exported_at == at


def test_the_template_has_no_export_time():
    content = workbook(Lists(), language="ru", workspace="Кабинет", base_currency="BYN")

    assert read_workbook(content).exported_at is None

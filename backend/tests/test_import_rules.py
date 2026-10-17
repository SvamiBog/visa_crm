"""Reading spreadsheet cells for the import (backlog 1.14): what the specialist typed by
hand becomes a value or a warning, never a guess."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.import_values import (
    Outcome,
    cell_text,
    name_key,
    parse_bool,
    parse_contacts,
    parse_date,
    parse_decision,
    parse_email,
    parse_int,
    parse_money,
    parse_outcome,
    parse_phone,
    parse_telegram,
    split_list,
)
from app.models.consultation import Decision
from app.models.visa_case import VisaEntries, VisaOutcome

TODAY = date(2026, 10, 17)


def test_cells_read_as_text() -> None:
    assert cell_text(None) is None
    assert cell_text("  ") is None
    assert cell_text(" Анна ") == "Анна"
    # Phones and IDs stored as numbers lose the «.0».
    assert cell_text(375291234567.0) == "375291234567"
    assert cell_text(12.5) == "12.5"
    assert cell_text(Decimal("40.00")) == "40"
    assert cell_text(datetime(2026, 6, 12, 0, 0)) == "12.06.2026"


def test_names_match_in_any_order_and_case() -> None:
    assert name_key("Иванова  Анна") == name_key("анна ИВАНОВА") == name_key("Анна Иванова.")
    assert name_key("Ёлкина Алёна") == name_key("Елкина Алена")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (datetime(2026, 6, 12, 14, 30), date(2026, 6, 12)),
        (date(2026, 6, 12), date(2026, 6, 12)),
        ("12.06.2026", date(2026, 6, 12)),
        ("1.6.2026", date(2026, 6, 1)),
        ("12/06/2026", date(2026, 6, 12)),
        ("12-06-2026", date(2026, 6, 12)),
        ("2026-06-12", date(2026, 6, 12)),
        ("12.06.2026 10:00", date(2026, 6, 12)),
        ("12.06.26", date(2026, 6, 12)),
        # A birthday: two digits more than 10 years ahead are the last century.
        ("03.04.85", date(1985, 4, 3)),
        ("03.04.36", date(2036, 4, 3)),
    ],
)
def test_dates(cell, expected) -> None:
    assert parse_date(cell, TODAY) == expected


@pytest.mark.parametrize("cell", ["32.01.2026", "12.13.2026", "12.06", "июнь", 45000, "", None])
def test_not_dates(cell) -> None:
    with pytest.raises(ValueError):
        parse_date(cell, TODAY)


def test_whole_numbers() -> None:
    assert parse_int(2) == 2
    assert parse_int(3.0) == 3
    assert parse_int("2 человека") == 2
    for cell in ("два", "2.5", 2.5, True):
        with pytest.raises(ValueError):
            parse_int(cell)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (350, (Decimal("350.00"), "BYN")),
        (120.5, (Decimal("120.50"), "BYN")),
        ("350", (Decimal("350.00"), "BYN")),
        ("350 р.", (Decimal("350.00"), "BYN")),
        ("350 руб", (Decimal("350.00"), "BYN")),
        ("1 200,50 BYN", (Decimal("1200.50"), "BYN")),
        ("1.200,50", (Decimal("1200.50"), "BYN")),
        ("1,200.50", (Decimal("1200.50"), "BYN")),
        ("€120", (Decimal("120.00"), "EUR")),
        ("120 евро", (Decimal("120.00"), "EUR")),
        ("$80", (Decimal("80.00"), "USD")),
        ("80 долларов", (Decimal("80.00"), "USD")),
        ("400 zł", (Decimal("400.00"), "PLN")),
        ("400 злотых", (Decimal("400.00"), "PLN")),
        ("12,345", (Decimal("12345.00"), "BYN")),
    ],
)
def test_money(cell, expected) -> None:
    assert parse_money(cell, "BYN") == expected


@pytest.mark.parametrize("cell", ["100 + 50", "100 BYN / 20 EUR", "-350", "бесплатно", True, ""])
def test_not_money(cell) -> None:
    with pytest.raises(ValueError):
        parse_money(cell, "BYN")


def test_several_currencies_in_one_cell_are_not_guessed() -> None:
    with pytest.raises(ValueError):
        parse_money("100 евро или 300 руб", "BYN")


def test_yes_and_no() -> None:
    for cell in ("да", "Да", "+", "✅", "оплачено", True, 1, "yes"):
        assert parse_bool(cell) is True, cell
    for cell in ("нет", "-", "не оплачено", False, 0, "no"):
        assert parse_bool(cell) is False, cell
    with pytest.raises(ValueError):
        parse_bool("частично")


def test_decisions() -> None:
    assert parse_decision("Да") == Decision.YES
    assert parse_decision("+") == Decision.YES
    assert parse_decision("в работе") == Decision.YES
    assert parse_decision("нет") == Decision.NO
    assert parse_decision("Отказался") == Decision.NO
    assert parse_decision("думает") == Decision.THINKING
    assert parse_decision("Подумают до осени") == Decision.THINKING
    assert parse_decision("?") == Decision.THINKING
    with pytest.raises(ValueError):
        parse_decision("позвонить")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Мульти на 2 года", Outcome(VisaOutcome.VISA, VisaEntries.MULTIPLE)),
        ("виза однократная", Outcome(VisaOutcome.VISA, VisaEntries.SINGLE)),
        ("двукратная виза", Outcome(VisaOutcome.VISA, VisaEntries.DOUBLE)),
        ("Одобрено", Outcome(VisaOutcome.VISA)),
        ("Виза после апелляции", Outcome(VisaOutcome.VISA, after_appeal=True)),
        ("отказ", Outcome(VisaOutcome.REFUSAL)),
        ("Отказ по апелляции", Outcome(VisaOutcome.REFUSAL, after_appeal=True)),
        ("не подались", Outcome(VisaOutcome.NOT_SUBMITTED)),
        ("Не подавались — передумали", Outcome(VisaOutcome.NOT_SUBMITTED)),
        ("отказались от подачи", Outcome(VisaOutcome.NOT_SUBMITTED)),
    ],
)
def test_visa_results(cell, expected) -> None:
    assert parse_outcome(cell) == expected


def test_unknown_result_is_not_guessed() -> None:
    with pytest.raises(ValueError):
        parse_outcome("ждём")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("+375 (29) 123-45-67", "+375291234567"),
        ("375291234567", "+375291234567"),
        (375291234567, "+375291234567"),
        ("8 029 123 45 67", "+375291234567"),
        ("80291234567", "+375291234567"),
        ("029 123-45-67", "+375291234567"),
        ("29 123 45 67", "+375291234567"),
        ("+48 600 123 456", "+48600123456"),
    ],
)
def test_phones(cell, expected) -> None:
    assert parse_phone(cell) == expected


def test_not_phones() -> None:
    for cell in ("12345", "нет телефона"):
        with pytest.raises(ValueError):
            parse_phone(cell)


def test_telegram() -> None:
    assert parse_telegram("@anna_k") == "anna_k"
    assert parse_telegram("https://t.me/anna_k") == "anna_k"
    assert parse_telegram("Анна К") == "Анна К"
    with pytest.raises(ValueError):
        parse_telegram("  ")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("+375 29 123-45-67 @anna_k", ("+375291234567", "anna_k")),
        ("@anna_k, 80291234567", ("+375291234567", "anna_k")),
        ("t.me/anna_k", (None, "anna_k")),
        ("anna_k", (None, "anna_k")),
        ("+375291234567", ("+375291234567", None)),
        # An email is not a Telegram nick.
        ("anna@mail.ru +375291234567", ("+375291234567", None)),
    ],
)
def test_contacts_cell(cell, expected) -> None:
    assert parse_contacts(cell) == expected


def test_contacts_cell_without_phone_or_nick() -> None:
    with pytest.raises(ValueError):
        parse_contacts("пишет в инсту")


def test_email_and_lists() -> None:
    assert parse_email(" anna@mail.ru ") == "anna@mail.ru"
    with pytest.raises(ValueError):
        parse_email("anna at mail")
    assert split_list("Польша, Италия; Испания") == ["Польша", "Италия", "Испания"]
    assert split_list(None) == []


# --- Files ---------------------------------------------------------------------------


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    import io

    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_xlsx_sheets_and_header() -> None:
    from app.core.import_files import FileError, read_table

    data = _xlsx(
        {
            "Польша": [["Польша с 08.06"], ["ФИО", "ID", None], [None], ["Анна", 123, None]],
            "Латвия": [["ФИО", "Дата"], ["Олег", datetime(2026, 6, 1)]],
        }
    )
    table = read_table(data)
    assert table.sheets == ["Польша", "Латвия"] and table.sheet == "Польша"
    # The title row is not the header; empty rows go, row numbers stay.
    assert (table.header_row, table.headers) == (2, ["ФИО", "ID"])
    assert [(r.number, r.cells) for r in table.rows] == [(4, ("Анна", 123))]
    latvia = read_table(data, "Латвия")
    assert latvia.rows[0].cells == ("Олег", datetime(2026, 6, 1))
    # The header row can be given.
    assert read_table(data, "Польша", header_row=1).headers == ["Польша с 08.06", ""]
    with pytest.raises(FileError, match="sheet_not_found"):
        read_table(data, "Италия")


def test_csv_delimiters_and_encodings() -> None:
    from app.core.import_files import read_table

    google = 'ФИО,Страны,Дата\n"Иванова Анна","Польша, Италия",12.05.2026\n'
    table = read_table(google.encode())
    assert table.rows[0].cells == ("Иванова Анна", "Польша, Италия", "12.05.2026")
    excel = "ФИО;Страна\r\nИванова Анна;Польша\r\n".encode("cp1251")
    assert read_table(excel).rows[0].cells == ("Иванова Анна", "Польша")


def test_files_that_are_not_read() -> None:
    import io
    import zipfile

    from app.core import import_files
    from app.core.import_files import FileError, read_table

    for data, code in (
        (b"", "file_empty"),
        (b"\xd0\xcf\x11\xe0legacy", "xls_not_supported"),
        (b"PK\x03\x04not a zip", "file_unreadable"),
        ("Только заголовок".encode(), "no_header"),
    ):
        with pytest.raises(FileError, match=code):
            read_table(data)
    # A small zip that unpacks into a lot: refused before it is opened.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * (import_files.MAX_UNPACKED + 1))
    with pytest.raises(FileError, match="file_too_large"):
        read_table(buffer.getvalue())

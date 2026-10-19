"""Reading spreadsheet cells for the import (backlog 1.14, 1.16): what the specialist typed
by hand becomes a value or a warning; a guess (a date without its year) is reported."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.import_values import (
    CellError,
    Outcome,
    cell_text,
    group_names,
    is_blank,
    is_nick,
    is_self_registered,
    looks_like_credentials,
    name_key,
    parse_bool,
    parse_contacts,
    parse_date,
    parse_decision,
    parse_email,
    parse_int,
    parse_loose_date,
    parse_money,
    parse_month,
    parse_outcome,
    parse_phone,
    parse_reference,
    parse_telegram,
    split_credentials,
    split_list,
    split_name,
    table_year,
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
        # A space before a four-digit year.
        ("17.12 2026", date(2026, 12, 17)),
        # Excel's number of days: a cell formatted as a number, a sheet saved as text.
        (46300, date(2026, 10, 5)),
        (46300.0, date(2026, 10, 5)),
        ("46300.0", date(2026, 10, 5)),
        ("46300", date(2026, 10, 5)),
    ],
)
def test_dates(cell, expected) -> None:
    assert parse_date(cell, TODAY) == expected


@pytest.mark.parametrize(
    "cell",
    ["32.01.2026", "12.13.2026", "12.06", "июнь", "06.04.0205", 2025, "17.08 15.57", "", None],
)
def test_not_dates(cell) -> None:
    with pytest.raises(ValueError):
        parse_date(cell, TODAY)


@pytest.mark.parametrize(
    ("cell", "year", "expected"),
    [
        ("12.06.2026", None, (date(2026, 6, 12), None)),
        ("до 25.12.2026", None, (date(2026, 12, 25), None)),
        ("до 17.12 2026", None, (date(2026, 12, 17), None)),
        # No year: the table's year, else this one — and the report says so.
        ("17.08 в 15.57", None, (date(2026, 8, 17), "date_without_year")),
        ("17.08", 2025, (date(2025, 8, 17), "date_without_year")),
        ("с 08.06", 2025, (date(2025, 6, 8), "date_without_year")),
        # A month: its 1st day.
        ("октябрь 2024", None, (date(2024, 10, 1), "date_month_only")),
        ("Август 2024", None, (date(2024, 8, 1), "date_month_only")),
    ],
)
def test_loose_dates(cell, year, expected) -> None:
    assert parse_loose_date(cell, TODAY, year) == expected


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        (2025, "year_only"),
        (2025.0, "year_only"),
        ("2025.0", "year_only"),
        ("06.04.0205", "bad_date"),
        ("31.02", "bad_date"),
        ("скоро", "bad_date"),
        ("потом 2024", "bad_date"),
    ],
)
def test_loose_dates_that_are_not_read(cell, reason) -> None:
    with pytest.raises(CellError) as error:
        parse_loose_date(cell, TODAY, None)
    assert error.value.reason == reason


def test_months_and_the_year_of_a_sheet() -> None:
    assert [parse_month(m) for m in ("Январь", "мая", "Май", "март", "окт.", "December")] == [
        1,
        5,
        5,
        3,
        10,
        12,
    ]
    with pytest.raises(ValueError):
        parse_month("весна")
    assert table_year("Подача25") == 2025
    assert table_year("Список Италия ЖО26") == 2026
    assert table_year("2024 Подача") == 2024
    assert table_year("Польша с 08.06", "Учёт.xlsx") is None
    assert table_year("Лист1", "Подача 2023.csv") == 2023


def test_whole_numbers() -> None:
    assert parse_int(2) == 2
    assert parse_int(3.0) == 3
    assert parse_int("2 человека") == 2
    # A sheet saved as text writes numbers with «.0».
    assert parse_int("2.0") == 2
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
        ("375291234567.0", "+375291234567"),
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
    # Only a nick tells people apart; a display name or a remark may be anyone.
    assert is_nick("anna_k") and not is_nick("Анна К") and not is_nick("в вайбере")
    assert not is_nick(None) and not is_nick("ann")


def test_blank_cells() -> None:
    for value in ("-", "—", "?", "...", "нет", "Нет", "n/a"):
        assert is_blank(value), value
    for value in ("0", "Анна", "-5", "да"):
        assert not is_blank(value), value


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Иванова Анна", ("Иванова Анна", None)),
        ("Иванова Анна (коллега Ольги)", ("Иванова Анна", "коллега Ольги")),
        ("Иванова Анна от Ольги Т.", ("Иванова Анна", "от Ольги Т.")),
        ("Иванова  Анна (сестра) от Ольги", ("Иванова Анна", "сестра; от Ольги")),
        ("Иванова Анна (повторно)", ("Иванова Анна", None)),
        ("Иванова Анна (дочь", ("Иванова Анна", "дочь")),
        ("Ivanova Anna", ("Ivanova Anna", None)),
    ],
)
def test_names_with_notes(cell, expected) -> None:
    assert split_name(cell) == expected


def test_several_people_in_one_cell() -> None:
    assert group_names("Иванова Анна") == ["Иванова Анна"]
    assert group_names("Иванова Анна + супруг + сын") == ["Иванова Анна", "супруг", "сын"]
    assert group_names("Иванова Анна и Петров Олег") == ["Иванова Анна", "Петров Олег"]
    assert group_names("Анна & Олег") == ["Анна", "Олег"]


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("anna@mail.ru", ("anna@mail.ru", None)),
        ("anna@mail.ru Pa55word", ("anna@mail.ru", "Pa55word")),
        ("anna@mail.ru, Pa55word", ("anna@mail.ru", "Pa55word")),
        ("логин: ITA123456 пароль: x1y2", ("ITA123456", "x1y2")),
        ("fwd@mail.ru Pa55 https://mail.ru/fwd", ("fwd@mail.ru", "Pa55")),
        ("Pa55 anna@mail.ru", ("anna@mail.ru", "Pa55")),
    ],
)
def test_login_and_password_in_one_cell(cell, expected) -> None:
    assert split_credentials(cell) == expected


def test_what_is_not_shown_back() -> None:
    assert looks_like_credentials("anna@mail.ru Pa55word")
    assert looks_like_credentials("логин anna пароль 123")
    assert not looks_like_credentials("anna@mail.ru")
    assert not looks_like_credentials("Иванова Анна")


def test_numbers_at_the_visa_center() -> None:
    assert parse_reference("PLB1234567890") == "PLB1234567890"
    assert parse_reference("12345") == "12345"
    assert parse_reference(12345.0) == "12345"
    assert parse_reference("12345.0") == "12345"
    assert parse_reference("anna@mail.ru URN: ITA123456789012") == "ITA123456789012"
    with pytest.raises(CellError, match="email_not_number"):
        parse_reference("anna@mail.ru")


def test_no_registrar() -> None:
    for value in ("я", "Я помогала", "самостоятельно", "Сама", "клиент сам", "без посредника"):
        assert is_self_registered(value), value
    for value in ("Наталья", "VisaStar", "Ямщикова"):
        assert not is_self_registered(value), value


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
    # A sheet saved as text: tabs, numbers with «.0», placeholder rows after the last client.
    tsv = "\tФИО\tСтоимость\n1.0\tИванова Анна\t100.0\n0.0\t\t0.0\n\t-\t0\n"
    table = read_table(tsv.encode())
    assert table.headers == ["", "ФИО", "Стоимость"]
    assert [r.cells for r in table.rows] == [("1.0", "Иванова Анна", "100.0")]


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

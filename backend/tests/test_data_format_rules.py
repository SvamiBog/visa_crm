"""The VisaCRM data format (backlog 1.21, docs/data-format.md): its sheets and columns, and
how strictly a cell is read — no guessing, a cell that does not fit is an error."""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.data_format import (
    ENTRIES,
    SEX,
    SHEETS,
    ColumnKind,
    ValueType,
)
from app.core.data_values import (
    CellError,
    cell_text,
    normalize,
    parse_bool,
    parse_choice,
    parse_currency,
    parse_date,
    parse_document_number,
    parse_email,
    parse_int,
    parse_list,
    parse_money,
    parse_percent,
    parse_phone,
    parse_rate,
    parse_telegram,
    parse_text,
)
from app.core.import_prompt import LANGUAGES, import_prompt

# --- The format --------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["ru", "en"])
def test_sheet_and_column_names_are_unique_in_each_language(language: str):
    sheets = [normalize(getattr(s, language)) for s in SHEETS]
    assert len(sheets) == len(set(sheets))
    for sheet in SHEETS:
        columns = [normalize(getattr(c, language)) for c in sheet.columns]
        assert len(columns) == len(set(columns)), sheet.key


def test_every_sheet_starts_with_its_own_code():
    for sheet in SHEETS:
        assert sheet.code.key == "code" and sheet.code.kind == ColumnKind.KEY, sheet.key
        assert sheet.code.points_to is None


def test_codes_point_to_sheets_of_the_format():
    keys = {s.key for s in SHEETS}
    for sheet in SHEETS:
        for column in sheet.columns:
            if column.points_to:
                assert column.points_to in keys
                assert column.type == ValueType.CODE


def test_choice_columns_have_words_in_both_languages():
    for sheet in SHEETS:
        for column in sheet.columns:
            assert (column.type == ValueType.CHOICE) == bool(column.choices), column.key
            for choice in column.choices:
                assert choice.ru and choice.en


def test_reference_columns_are_never_required():
    for sheet in SHEETS:
        for column in sheet.columns:
            if column.kind == ColumnKind.REF:
                assert not column.required


# --- Text and headers --------------------------------------------------------------------


def test_headers_compare_without_case_yo_and_punctuation():
    assert normalize(" Доп. Идентификатор ") == normalize("доп идентификатор")
    assert normalize("Партнёр") == normalize("партнер")
    assert normalize("Код клиента*") == normalize("код клиента")


def test_whole_numbers_read_as_text_without_a_fraction():
    assert cell_text(17.0) == "17"
    assert cell_text(" ") is None
    assert cell_text(None) is None


def test_text_longer_than_the_column_allows_is_an_error():
    assert parse_text("  Петрова  ", 200) == "Петрова"
    with pytest.raises(CellError, match="too_long"):
        parse_text("x" * 201, 200)


def test_document_numbers_are_letters_and_digits_in_upper_case():
    assert parse_document_number("mp 1234567") == "MP1234567"
    with pytest.raises(CellError, match="document_number"):
        parse_document_number("MP-1234567")


# --- Dates -------------------------------------------------------------------------------


def test_a_date_is_a_date_cell_or_day_month_year():
    assert parse_date(datetime(2025, 6, 5, 0, 0)) == date(2025, 6, 5)
    assert parse_date(date(2025, 6, 5)) == date(2025, 6, 5)
    assert parse_date("05.06.2025") == date(2025, 6, 5)
    assert parse_date("5.6.2025") == date(2025, 6, 5)
    assert parse_date("2025-06-05") == date(2025, 6, 5)


@pytest.mark.parametrize(
    "cell", ["17.08", "октябрь 2024", "до 25.12.2026", "17.12 2026", "31.02.2025", "2024", 45678]
)
def test_dates_without_a_year_in_words_or_as_numbers_are_errors(cell):
    with pytest.raises(CellError, match="date"):
        parse_date(cell)


# --- Numbers and money -------------------------------------------------------------------


def test_money_is_a_number_with_a_dot_or_a_comma():
    assert parse_money(150) == Decimal("150.00")
    assert parse_money(732.5) == Decimal("732.50")
    assert parse_money("1 234,5") == Decimal("1234.50")
    assert parse_money("0") == Decimal("0.00")


@pytest.mark.parametrize("cell", ["150 BYN", "150+50", "-20", "сто"])
def test_money_with_a_currency_a_sum_or_a_minus_is_an_error(cell):
    with pytest.raises(CellError, match="money"):
        parse_money(cell)


def test_rates_and_percents_are_positive_numbers():
    assert parse_rate("3,55") == Decimal("3.55")
    assert parse_rate(0.8125) == Decimal("0.8125")
    assert parse_percent(10) == Decimal("10.00")
    with pytest.raises(CellError, match="rate"):
        parse_rate(0)
    with pytest.raises(CellError, match="percent"):
        parse_percent(150)


def test_whole_numbers():
    assert parse_int(2) == 2
    assert parse_int("3") == 3
    with pytest.raises(CellError, match="number"):
        parse_int(2.5)


def test_currencies_are_the_codes_the_crm_keeps_accounts_in():
    assert parse_currency("eur") == "EUR"
    assert parse_currency("BYN") == "BYN"
    for cell in ("RUB", "руб.", "евро"):
        with pytest.raises(CellError, match="currency"):
            parse_currency(cell)


# --- Yes / no and fixed words ------------------------------------------------------------


def test_yes_and_no_in_both_languages():
    assert parse_bool("Да") is True
    assert parse_bool("no") is False
    assert parse_bool(True) is True
    for cell in ("+", "✓", "оплачено"):
        with pytest.raises(CellError, match="yes_no"):
            parse_bool(cell)


def test_fixed_words_in_either_language_and_any_case():
    assert parse_choice("Многократная", ENTRIES) == "multiple"
    assert parse_choice("single", ENTRIES) == "single"
    with pytest.raises(CellError, match="choice"):
        parse_choice("мульти", ENTRIES)


def test_sex_reads_the_cyrillic_and_the_latin_letter():
    assert parse_choice("М", SEX) == "M"
    assert parse_choice("M", SEX) == "M"
    assert parse_choice("ж", SEX) == "F"


def test_several_values_are_separated_by_commas():
    assert parse_list("Польша, Латвия ,") == ["Польша", "Латвия"]


# --- Contacts ----------------------------------------------------------------------------


def test_phones_are_international():
    assert parse_phone("+375291234567") == "+375291234567"
    assert parse_phone("+375 (29) 123-45-67") == "+375291234567"


@pytest.mark.parametrize("cell", ["80291234567", "291234567", 375291234567, "+37529"])
def test_local_or_short_phones_are_errors(cell):
    with pytest.raises(CellError, match="phone"):
        parse_phone(cell)


def test_telegram_is_a_nick_kept_without_the_at():
    assert parse_telegram("@anna_iv") == "anna_iv"
    assert parse_telegram("anna_iv") == "anna_iv"
    for cell in ("в вайбере", "t.me/anna_iv", "@ab"):
        with pytest.raises(CellError, match="telegram"):
            parse_telegram(cell)


def test_one_email_address():
    assert parse_email(" anna@mail.ru ") == "anna@mail.ru"
    for cell in ("anna@mail.ru Pa55word", "anna"):
        with pytest.raises(CellError, match="email"):
            parse_email(cell)


# --- The prompt --------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_prompt_lists_every_sheet_and_column_of_the_format(language: str):
    text = import_prompt(language)
    for sheet in SHEETS:
        title = getattr(sheet, language)
        match = re.search(rf"^{re.escape(title)}: (.+)$", text, re.M)
        assert match, title
        columns = [c.strip() for c in match.group(1).split(" | ")]
        names = [re.sub(r"\s*\(.*?\)", "", c).rstrip("*") for c in columns]
        assert names == [getattr(c, language) for c in sheet.columns], title
        starred = {re.sub(r"\s*\(.*?\)", "", c).rstrip("*") for c in columns if "*" in c}
        assert starred == {getattr(c, language) for c in sheet.columns if c.required}, title


def test_the_russian_prompt_is_the_one_in_the_docs():
    docs = (Path(__file__).resolve().parents[2] / "docs" / "import-prompt.md").read_text("utf-8")
    block = docs.split("````text\n", 1)[1].split("\n````", 1)[0]
    assert block + "\n" == import_prompt("ru")

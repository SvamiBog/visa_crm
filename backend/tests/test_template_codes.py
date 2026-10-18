"""The codes of document templates (backlog 2.1): how they are written, what they insert
and how the filled file is named."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.templates import (
    CODES,
    Localized,
    Money,
    file_name,
    filler,
    format_value,
    missing,
    normalize,
    resolve,
    split_codes,
)

DATA = {
    "full_name": "Иван Тестов",
    "full_name_latin": "TESTOV IVAN",
    "birth_date": date(1990, 10, 5),
    "nationality": Localized("Беларусь", "Belarus"),
    "price": Money(Decimal("1200.5"), "BYN"),
    "case_number": 17,
    "phone": "",
}


def test_every_code_is_written_one_way_per_meaning():
    seen: dict[str, str] = {}
    for code in CODES:
        assert code.ru and code.en
        for alias in (*code.ru, *code.en):
            assert seen.setdefault(normalize(alias), code.key) == code.key, alias
    assert len({c.key for c in CODES}) == len(CODES)


@pytest.mark.parametrize(
    ("written", "key", "lang"),
    [
        ("ФИО", "full_name", "ru"),
        ("  фио ", "full_name", "ru"),
        ("Дата рождения", "birth_date", "ru"),
        ("дата_рождения", "birth_date", "ru"),
        ("ДАТА  РОЖДЕНИЯ", "birth_date", "ru"),
        ("Date of birth", "birth_date", "en"),
        ("date_of_birth", "birth_date", "en"),
        ("Гражданство", "nationality", "ru"),
        ("nationality", "nationality", "en"),
        ("Дата подачи", "submission_date", "ru"),
        ("Сегодня", "today", "ru"),
    ],
)
def test_case_spaces_and_underscores_do_not_matter(written: str, key: str, lang: str):
    found = resolve(written)
    assert found is not None
    assert (found[0].key, found[1]) == (key, lang)


def test_yo_and_ye_are_the_same_letter():
    # «е» typed for «ё» (or the other way round) is still the same code.
    assert normalize("Ёлка") == normalize("елка")


def test_unknown_codes():
    assert resolve("Фамлия") is None
    assert resolve("") is None


def test_values_in_the_template_language():
    assert format_value(date(1990, 10, 5), "ru") == "05.10.1990"
    assert format_value(date(1990, 10, 5), "en") == "05.10.1990"
    assert format_value(Localized("Беларусь", "Belarus"), "en") == "Belarus"
    assert format_value(Money(Decimal("1200.5"), "BYN"), "ru") == "1 200,50 BYN"
    assert format_value(Money(Decimal("1200.5"), "BYN"), "en") == "1,200.50 BYN"
    assert format_value(Money(Decimal("0"), "EUR"), "ru") == "0,00 EUR"
    assert format_value(17, "ru") == "17"
    assert format_value(None, "ru") == ""
    assert format_value("  Иван  ", "ru") == "Иван"


def test_the_code_language_decides_the_value_language():
    value_of = filler(DATA)
    assert value_of("Гражданство") == "Беларусь"
    assert value_of("Nationality") == "Belarus"
    assert value_of("Стоимость") == "1 200,50 BYN"
    assert value_of("price") == "1,200.50 BYN"
    # Known but not filled in: empty; unknown: left for the file to show.
    assert value_of("Телефон") == ""
    assert value_of("Номер паспорта") == ""
    assert value_of("Фамлия") is None


def test_split_codes_into_known_and_unknown():
    known, unknown = split_codes(["ФИО", "Full name", "Фамлия", "Дата рождения", "фамлия"])
    assert known == ["full_name", "birth_date"]  # once each, in the order they come
    assert unknown == ["Фамлия"]


def test_missing_values():
    assert missing(["full_name", "phone", "passport_number", "case_number"], DATA) == [
        "phone",
        "passport_number",
    ]


def test_file_name_by_default_and_by_pattern():
    assert file_name(None, "Спонсорское письмо", DATA) == "Спонсорское письмо — Иван Тестов.docx"
    assert file_name(None, "Анкета", {}) == "Анкета.docx"
    assert (
        file_name("{{ФИО латиницей}} cover letter {{Дата рождения}}", "x", DATA)
        == "TESTOV IVAN cover letter 05.10.1990.docx"
    )
    # Characters a file name cannot have, and empty values, leave no traces.
    assert file_name('Письмо: "{{ФИО}}" / {{Телефон}}  ', "x", DATA) == "Письмо Иван Тестов.docx"
    assert file_name("{{Телефон}}", "x", DATA) == "document.docx"
    long = file_name("Я" * 300, "x", DATA)
    assert len(long) == 150 + len(".docx")

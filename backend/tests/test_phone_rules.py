import pytest

from app.core.phone import normalize_phone, phone_search_digits


@pytest.mark.parametrize(
    "typed",
    [
        "8 029 555 01 23",
        "8029 5550123",
        "8 (029) 555-01-23",
        "029 555-01-23",
        "(029) 555 01 23",
        "29 555 01 23",
        "+375 29 555-01-23",
        "375295550123",
        "00375295550123",
    ],
)
def test_belarusian_numbers_typed_the_local_way_get_the_country_code(typed):
    assert normalize_phone(typed) == "+375295550123"


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("33 555 01 23", "+375335550123"),
        ("25 555 01 23", "+375255550123"),
        ("44 555 01 23", "+375445550123"),
        # A landline: «8 0» and «0» are Belarusian whatever the code.
        ("8 017 123-45-67", "+375171234567"),
        ("017 123-45-67", "+375171234567"),
    ],
)
def test_other_belarusian_codes(typed, stored):
    assert normalize_phone(typed) == stored


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("+48 600 123 456", "+48600123456"),
        ("0048 600 123 456", "+48600123456"),
        # With «+» the number is taken as typed, even if it looks local.
        ("+8 029 555 01 23", "+80295550123"),
        # Nine digits without a mobile code are not guessed to be Belarusian.
        ("123 456 789", "+123456789"),
    ],
)
def test_numbers_with_a_country_code_are_kept(typed, stored):
    assert normalize_phone(typed) == stored


@pytest.mark.parametrize("typed", ["123", "+1 234", "телефон"])
def test_too_short_numbers_are_refused(typed):
    with pytest.raises(ValueError):
        normalize_phone(typed)


@pytest.mark.parametrize(
    ("typed", "forms"),
    [
        ("0123", {"0123"}),  # the end of a number stays as typed
        ("29 555", {"29555"}),
        ("8 029 555", {"8029555", "29555"}),
        ("029 555-01-23", {"0295550123", "295550123"}),
        ("+375 29 555", {"37529555"}),
    ],
)
def test_search_also_tries_the_number_without_the_local_prefix(typed, forms):
    assert phone_search_digits(typed) == forms

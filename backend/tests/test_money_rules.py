"""The order of the currencies a specialist accepts (app/core/money.py). The formulas are
tests/test_finance_rules.py, the settings through the API — tests/test_base_currency.py."""

from app.core.money import CURRENCIES, ordered_currencies


def test_the_base_currency_comes_first_and_the_rest_keep_one_order():
    assert ordered_currencies("BYN", ["EUR", "USD"]) == ["BYN", "USD", "EUR"]
    assert ordered_currencies("BYN", ["USD", "EUR"]) == ["BYN", "USD", "EUR"]
    assert ordered_currencies("EUR", ["USD", "BYN", "PLN"]) == ["EUR", "BYN", "PLN", "USD"]


def test_payments_in_the_base_currency_are_always_possible():
    assert ordered_currencies("PLN", []) == ["PLN"]


def test_the_base_currency_is_listed_once():
    assert ordered_currencies("USD", ["USD", "EUR", "USD"]) == ["USD", "EUR"]


def test_a_currency_chosen_twice_is_listed_once():
    assert ordered_currencies("BYN", ["EUR", "EUR"]) == ["BYN", "EUR"]


def test_a_currency_the_site_does_not_keep_is_left_out():
    assert ordered_currencies("BYN", ["GBP", "EUR"]) == ["BYN", "EUR"]


def test_every_currency_chosen_gives_the_whole_list_once():
    for base in CURRENCIES:
        ordered = ordered_currencies(base, list(CURRENCIES))
        assert ordered[0] == base
        assert sorted(ordered) == sorted(CURRENCIES)

"""The place a new entry takes in a list of purposes, channels or vault systems
(app/core/dictionaries.py, backlog 1.15.14). The lists through the API are
tests/test_dictionaries.py."""

import pytest

from app.core.dictionaries import SORT_ORDER_MAX, next_sort_order


def test_the_first_entry_of_an_empty_list_takes_the_first_place():
    assert next_sort_order(None) == 0


@pytest.mark.parametrize(("last", "place"), [(0, 1), (5, 6), (SORT_ORDER_MAX - 1, SORT_ORDER_MAX)])
def test_a_new_entry_goes_after_the_last_one(last: int, place: int):
    assert next_sort_order(last) == place


def test_a_full_list_keeps_new_entries_at_the_highest_place():
    """Adding to a list never fails on its order: the place stops at the highest one the
    API takes."""
    assert next_sort_order(SORT_ORDER_MAX) == SORT_ORDER_MAX
    assert next_sort_order(SORT_ORDER_MAX + 1) == SORT_ORDER_MAX

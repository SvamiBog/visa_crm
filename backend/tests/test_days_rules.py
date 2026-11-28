"""Which days the CRM takes (app/core/days.py): the years the work is about, and no year
typed wrong. Where a date comes in — the API — is tests/test_days.py."""

from datetime import date

from app.core.days import FIRST_DAY, LAST_DAY, in_range


def test_the_days_taken_are_the_years_the_work_is_about():
    assert in_range(FIRST_DAY) and in_range(LAST_DAY) and in_range(date(2026, 11, 10))
    assert not in_range(date(26, 11, 5)) and not in_range(date(1899, 12, 30))
    assert not in_range(date(2101, 1, 1)) and not in_range(date.max)

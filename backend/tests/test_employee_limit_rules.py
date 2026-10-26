"""Daily limits of an employee (backlog Т.14): app/core/employee_limits.py."""

from datetime import UTC, datetime

import pytest

from app.core import clock, employee_limits
from app.core.employee_limits import over_limit


def test_the_limit_counts_different_cards():
    assert not over_limit([], "a", 2)
    assert not over_limit(["a"], "b", 2)
    assert over_limit(["a", "b"], "c", 2)


def test_a_card_opened_today_opens_again_past_the_limit():
    assert not over_limit(["a", "b"], "a", 2)
    assert not over_limit(["a", "b", "c"], "b", 2)


def test_nothing_opens_past_a_spent_limit_even_after_a_lower_one_is_set():
    """The specialist lowered the limit during the day: what was opened stays open."""
    assert over_limit(["a", "b", "c"], "d", 2)
    assert not over_limit(["a", "b", "c"], "c", 2)


@pytest.mark.parametrize(
    ("now", "timezone", "start"),
    [
        # 01:30 in Minsk (UTC+3) is still the evening before in UTC.
        (datetime(2026, 10, 25, 22, 30, tzinfo=UTC), "Europe/Minsk", datetime(2026, 10, 25, 21, 0)),
        (datetime(2026, 10, 25, 20, 59, tzinfo=UTC), "Europe/Minsk", datetime(2026, 10, 24, 21, 0)),
        (datetime(2026, 10, 25, 10, 0, tzinfo=UTC), "Europe/Warsaw", datetime(2026, 10, 24, 22, 0)),
    ],
)
def test_the_day_starts_at_midnight_in_the_specialists_time_zone(
    monkeypatch: pytest.MonkeyPatch, now: datetime, timezone: str, start: datetime
):
    monkeypatch.setattr(clock, "_now", lambda: now)
    assert employee_limits.day_start(timezone) == start.replace(tzinfo=UTC)

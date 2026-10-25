"""Today in a workspace's time zone (CLAUDE.md → «Время»): deadlines, waits and
reminders count from it. Every «today» of the app comes from here, so the tests hold
this one clock still for the length of a test (tests/conftest.py).
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Minsk"


def _now() -> datetime:
    return datetime.now(UTC)


def today_in(timezone: str | None) -> date:
    """The calendar day in the time zone; Europe/Minsk for a workspace without one."""
    return _now().astimezone(ZoneInfo(timezone or DEFAULT_TIMEZONE)).date()

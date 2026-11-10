"""The calendar days the CRM takes (CLAUDE.md → «Время»): a date typed into a form, sent to
the API or read from an import file lies within the years the work can be about.

A year typed wrong gets through a browser's date field («26» stays the year 26, «9999» is
a date too) and through a spreadsheet («05.11.0026» is a date there as well). Such a date
is never «soon»: a submission in the year 26 comes up in no reminder and on no list, and
at the ends of the calendar counting days from it fails. So it is refused where it comes
in, and the specialist sees it at once.
"""

from datetime import date
from typing import Annotated

from pydantic import Field

# Nobody alive was born earlier, and no visa, passport or booking runs later.
FIRST_DAY = date(1900, 1, 1)
LAST_DAY = date(2100, 12, 31)

# A date of a request: every date field and date parameter of the API is one
# (tests/test_days.py checks it).
Day = Annotated[date, Field(ge=FIRST_DAY, le=LAST_DAY)]


def in_range(day: date) -> bool:
    return FIRST_DAY <= day <= LAST_DAY

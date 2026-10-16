"""Analytics rules (backlog 1.13; docs/clients-module.md → «Аналитика»). Pure functions:
the API counts the cases of a period and sorts them with these.

- A case counts in the period of its submission day: the appointment date, or the decision
  day when no date was entered.
- A result is one of: a visa by kind (single, double, multiple by its length), a visa or a
  refusal after an appeal, a refusal, or «не подались» (had a date, did not go).
- Approval is visas out of decisions (visas and refusals), as a percent.
"""

from collections.abc import Iterable
from datetime import date
from enum import StrEnum

from app.core.consultation_checks import add_months
from app.models.visa_case import VisaEntries, VisaOutcome

# A multiple visa up to three months, up to a year, or longer (the rest: no dates given).
SHORT_DAYS = 92
YEAR_DAYS = 366


class Result(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    MULTIPLE_SHORT = "multiple_short"
    MULTIPLE_YEAR = "multiple_year"
    MULTIPLE_LONG = "multiple_long"
    MULTIPLE = "multiple"  # validity not entered
    VISA = "visa"  # entries not entered
    VISA_AFTER_APPEAL = "visa_after_appeal"
    REFUSAL = "refusal"
    REFUSAL_AFTER_APPEAL = "refusal_after_appeal"
    NOT_SUBMITTED = "not_submitted"


def result(
    outcome: VisaOutcome,
    *,
    after_appeal: bool = False,
    entries: VisaEntries | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> Result:
    if outcome == VisaOutcome.NOT_SUBMITTED:
        return Result.NOT_SUBMITTED
    if outcome == VisaOutcome.REFUSAL:
        return Result.REFUSAL_AFTER_APPEAL if after_appeal else Result.REFUSAL
    if after_appeal:
        return Result.VISA_AFTER_APPEAL
    match entries:
        case VisaEntries.SINGLE:
            return Result.SINGLE
        case VisaEntries.DOUBLE:
            return Result.DOUBLE
        case VisaEntries.MULTIPLE:
            if valid_from is None or valid_to is None:
                return Result.MULTIPLE
            days = (valid_to - valid_from).days + 1
            if days <= SHORT_DAYS:
                return Result.MULTIPLE_SHORT
            return Result.MULTIPLE_YEAR if days <= YEAR_DAYS else Result.MULTIPLE_LONG
        case _:
            return Result.VISA


def is_visa(r: Result) -> bool:
    return r not in (Result.REFUSAL, Result.REFUSAL_AFTER_APPEAL, Result.NOT_SUBMITTED)


def approval(visas: int, refusals: int) -> int | None:
    """Percent of decisions that were visas; None before any decision."""
    decided = visas + refusals
    return round(visas * 100 / decided) if decided else None


def months(start: date, end: date) -> list[date]:
    """First days of the months the period touches, in order."""
    first, last = start.replace(day=1), end.replace(day=1)
    out = []
    while first <= last:
        out.append(first)
        first = add_months(first, 1)
    return out


def average(values: Iterable[int]) -> int | None:
    values = list(values)
    return round(sum(values) / len(values)) if values else None

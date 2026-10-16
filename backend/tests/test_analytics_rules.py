"""Analytics rules (app/core/analytics.py): results of cases, approval, months."""

from datetime import date

from app.core.analytics import Result, approval, average, is_visa, months, result
from app.models.visa_case import VisaEntries, VisaOutcome

VISA, REFUSAL = VisaOutcome.VISA, VisaOutcome.REFUSAL


def multiple(valid_from: date, valid_to: date) -> Result:
    return result(VISA, entries=VisaEntries.MULTIPLE, valid_from=valid_from, valid_to=valid_to)


def test_a_visa_is_sorted_by_entries_and_length():
    assert result(VISA, entries=VisaEntries.SINGLE) == Result.SINGLE
    assert result(VISA, entries=VisaEntries.DOUBLE) == Result.DOUBLE
    assert result(VISA) == Result.VISA  # entries not entered
    assert result(VISA, entries=VisaEntries.MULTIPLE) == Result.MULTIPLE
    # Up to three months (92 days counting both ends), up to a year, longer.
    assert multiple(date(2026, 5, 1), date(2026, 7, 31)) == Result.MULTIPLE_SHORT
    assert multiple(date(2026, 5, 1), date(2026, 8, 1)) == Result.MULTIPLE_YEAR
    assert multiple(date(2026, 5, 1), date(2027, 4, 30)) == Result.MULTIPLE_YEAR
    assert multiple(date(2026, 5, 1), date(2031, 4, 30)) == Result.MULTIPLE_LONG


def test_appeals_refusals_and_not_submitted():
    assert result(VISA, after_appeal=True, entries=VisaEntries.SINGLE) == Result.VISA_AFTER_APPEAL
    assert result(REFUSAL) == Result.REFUSAL
    assert result(REFUSAL, after_appeal=True) == Result.REFUSAL_AFTER_APPEAL
    assert result(VisaOutcome.NOT_SUBMITTED) == Result.NOT_SUBMITTED
    assert is_visa(Result.VISA_AFTER_APPEAL) and is_visa(Result.MULTIPLE_LONG)
    assert not is_visa(Result.REFUSAL_AFTER_APPEAL) and not is_visa(Result.NOT_SUBMITTED)


def test_approval_months_and_averages():
    assert approval(31, 2) == 94
    assert approval(0, 0) is None
    assert months(date(2026, 5, 20), date(2026, 10, 3)) == [date(2026, m, 1) for m in range(5, 11)]
    assert months(date(2026, 12, 5), date(2027, 1, 2)) == [date(2026, 12, 1), date(2027, 1, 1)]
    assert average([96, 151, 128]) == 125 and average([]) is None

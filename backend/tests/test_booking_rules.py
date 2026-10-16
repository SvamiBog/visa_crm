"""What the system watches in bookings (docs/clients-module.md → «Брони»)."""

from datetime import date

from app.core.bookings import BookingWarning, days_left, deadline, warnings
from app.models import BookingKind, BookingStatus, CaseStage

TODAY = date(2026, 10, 16)


def check(**fields):
    base = {
        "kind": BookingKind.HOTEL,
        "status": BookingStatus.ACTIVE,
        "free_cancel_until": None,
        "valid_until": None,
        "stage": CaseStage.DOCUMENTS,
        "submission_on": None,
        "today": TODAY,
    }
    return warnings(**{**base, **fields})


def test_deadline_is_the_cancellation_date_or_the_flight_validity():
    assert deadline(BookingKind.HOTEL, date(2026, 10, 20), None) == date(2026, 10, 20)
    assert deadline(BookingKind.FLIGHT, None, date(2026, 11, 1)) == date(2026, 11, 1)
    assert deadline(BookingKind.HOTEL, None, None) is None
    assert days_left(date(2026, 10, 19), TODAY) == 3
    assert days_left(date(2026, 10, 15), TODAY) == -1
    assert days_left(None, TODAY) is None


def test_a_closed_case_with_a_live_booking_is_flagged():
    assert check(stage=CaseStage.RESULT) == [BookingWarning.CASE_CLOSED]
    assert check(stage=CaseStage.RESULT, status=BookingStatus.RESCHEDULED) == [
        BookingWarning.CASE_CLOSED
    ]
    # A cancelled booking needs nothing.
    assert check(stage=CaseStage.RESULT, status=BookingStatus.CANCELLED) == []


def test_free_cancellation_that_has_passed_is_flagged():
    assert check(free_cancel_until=date(2026, 10, 15)) == [BookingWarning.CANCELLATION_PASSED]
    assert check(free_cancel_until=TODAY) == []  # still today


def test_flight_booking_that_ends_before_the_submission_is_flagged():
    flight = {"kind": BookingKind.FLIGHT, "submission_on": date(2026, 11, 20)}
    assert check(**flight, valid_until=date(2026, 11, 19)) == [
        BookingWarning.EXPIRES_BEFORE_SUBMISSION
    ]
    assert check(**flight, valid_until=date(2026, 11, 20)) == []
    assert check(**flight) == []  # no validity yet
    assert check(**flight, valid_until=date(2026, 11, 19), stage=CaseStage.DOCUMENTS) == [
        BookingWarning.EXPIRES_BEFORE_SUBMISSION
    ]
    # Already submitted: the booking has done its job.
    assert check(**flight, valid_until=date(2026, 11, 19), stage=CaseStage.REVIEW) == []

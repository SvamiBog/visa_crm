"""What the system watches in bookings (docs/clients-module.md → «Брони»).

A hotel's deadline is its last day of free cancellation; a client's flight booking has a
validity date instead. Reminders 3 and 1 days before a deadline come with the in-CRM
reminders (backlog 1.11) and use these rules.
"""

from datetime import date
from enum import StrEnum

from app.core.queues import SUBMITTED_STAGES
from app.models import BookingKind, BookingStatus, CaseStage

# Watched until cancelled: a rescheduled booking is still a live booking.
LIVE = (BookingStatus.ACTIVE, BookingStatus.RESCHEDULED)
# Days before a deadline to remind (backlog 1.11); the deadlines view marks them.
REMIND_DAYS = (3, 1)


class BookingWarning(StrEnum):
    # The case reached its result and the booking is still live: cancel it.
    CASE_CLOSED = "case_closed"
    # The free cancellation day has passed and the booking is still live.
    CANCELLATION_PASSED = "cancellation_passed"
    # The client's flight booking ends before the submission date.
    EXPIRES_BEFORE_SUBMISSION = "expires_before_submission"


def deadline(
    kind: BookingKind, free_cancel_until: date | None, valid_until: date | None
) -> date | None:
    return free_cancel_until if kind == BookingKind.HOTEL else valid_until


def days_left(day: date | None, today: date) -> int | None:
    """Negative once the day has passed."""
    return None if day is None else (day - today).days


def warnings(
    *,
    kind: BookingKind,
    status: BookingStatus,
    free_cancel_until: date | None,
    valid_until: date | None,
    stage: CaseStage,
    submission_on: date | None,
    today: date,
) -> list[BookingWarning]:
    if status not in LIVE:
        return []
    found = []
    if stage == CaseStage.RESULT:
        found.append(BookingWarning.CASE_CLOSED)
    if free_cancel_until is not None and free_cancel_until < today:
        found.append(BookingWarning.CANCELLATION_PASSED)
    if (
        kind == BookingKind.FLIGHT
        and stage not in SUBMITTED_STAGES
        and valid_until is not None
        and submission_on is not None
        and valid_until < submission_on
    ):
        found.append(BookingWarning.EXPIRES_BEFORE_SUBMISSION)
    return found

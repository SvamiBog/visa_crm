"""Waiting list and application attempt rules (docs/clients-module.md → «Очереди и
попытки записи»). Pure functions with «today» passed in, and the one condition every
count of open applications shares (`open_attempt`).
"""

from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import ColumnElement, and_

from app.models.queue import ApplicationAttempt, AttemptOutcome
from app.models.visa_case import CaseStage, VisaCase


class QueueStatus(StrEnum):
    IN_QUEUE = "in_queue"
    DATE_RECEIVED = "date_received"
    SUBMITTED = "submitted"
    REMOVED = "removed"


class AttemptStatus(StrEnum):
    OPEN = "open"
    DATE_RECEIVED = "date_received"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


SUBMITTED_STAGES = (CaseStage.REVIEW, CaseStage.RESULT)


def open_attempt() -> ColumnElement[bool]:
    """An application still open, for a query that joins its case: no outcome marked and
    the case not yet submitted. Once the case is in review or has its result the window
    no longer matters, so the summary of the queues, the home page and the reminder of
    the window's end leave it out, as the country's page does; the attempt itself is
    kept as it was (audit 2026-11-27, H3)."""
    return and_(ApplicationAttempt.outcome.is_(None), VisaCase.stage.not_in(SUBMITTED_STAGES))


def queue_status(
    *, letter_on: date | None, removed_on: date | None, case_stage: CaseStage
) -> QueueStatus:
    """В очереди / дата получена / подан / снят — from the dates, never typed."""
    if removed_on is not None:
        return QueueStatus.REMOVED
    if letter_on is not None:
        return (
            QueueStatus.SUBMITTED if case_stage in SUBMITTED_STAGES else QueueStatus.DATE_RECEIVED
        )
    return QueueStatus.IN_QUEUE


def days_waiting(
    queued_on: date, *, letter_on: date | None, removed_on: date | None, today: date
) -> int:
    """From the queue to the letter; still waiting — to today; taken off — to that day."""
    end = letter_on or removed_on or today
    return max((end - queued_on).days, 0)


def deadline(sent_on: date, window_days: int) -> date:
    return sent_on + timedelta(days=window_days)


def attempt_status(outcome: AttemptOutcome | None, deadline_on: date, today: date) -> AttemptStatus:
    """An open attempt past its deadline has expired, whether or not it was marked."""
    if outcome is not None:
        return AttemptStatus(outcome.value)
    return AttemptStatus.EXPIRED if today > deadline_on else AttemptStatus.OPEN

"""Waiting list and application attempt rules (docs/clients-module.md → «Очереди и
попытки записи»). Pure functions with «today» passed in.
"""

from datetime import date, timedelta
from enum import StrEnum

from app.models.queue import AttemptOutcome
from app.models.visa_case import CaseStage


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

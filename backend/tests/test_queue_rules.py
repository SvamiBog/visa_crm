from datetime import date

from app.core.queues import (
    AttemptStatus,
    QueueStatus,
    attempt_status,
    days_waiting,
    deadline,
    queue_status,
)
from app.models import AttemptOutcome, CaseStage


def test_queue_status_comes_from_the_dates():
    def status(letter=None, removed=None, stage=CaseStage.WAITING):
        return queue_status(letter_on=letter, removed_on=removed, case_stage=stage)

    assert status() == QueueStatus.IN_QUEUE
    assert status(letter=date(2026, 10, 10)) == QueueStatus.DATE_RECEIVED
    assert status(letter=date(2026, 10, 10), stage=CaseStage.REVIEW) == QueueStatus.SUBMITTED
    assert status(removed=date(2026, 9, 1)) == QueueStatus.REMOVED


def test_days_waiting():
    queued = date(2026, 6, 12)
    today = date(2026, 10, 16)
    assert days_waiting(queued, letter_on=date(2026, 10, 10), removed_on=None, today=today) == 120
    assert days_waiting(queued, letter_on=None, removed_on=None, today=today) == 126
    assert days_waiting(queued, letter_on=None, removed_on=date(2026, 7, 12), today=today) == 30


def test_attempt_window_and_status():
    sent = date(2026, 8, 1)
    end = deadline(sent, 60)
    assert end == date(2026, 9, 30)
    assert attempt_status(None, end, today=date(2026, 9, 30)) == AttemptStatus.OPEN
    # Past the deadline it has expired even if nobody marked it.
    assert attempt_status(None, end, today=date(2026, 10, 1)) == AttemptStatus.EXPIRED
    assert (
        attempt_status(AttemptOutcome.DATE_RECEIVED, end, today=date(2026, 10, 1))
        == AttemptStatus.DATE_RECEIVED
    )

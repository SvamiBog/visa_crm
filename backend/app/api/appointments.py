"""How a case gets its appointment (backlog 1.6): its waiting list entries and application
attempts as shown on the case, and the average wait. Endpoints are in app/api/queues.py.
"""

import uuid
from datetime import date

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queues import (
    AttemptStatus,
    QueueStatus,
    attempt_status,
    days_waiting,
    queue_status,
)
from app.models import (
    ApplicationAttempt,
    AttemptOutcome,
    VisaCase,
    WaitingListEntry,
)
from app.models.visa_case import CaseStage

# Where averages are kept: country, city (visa center) and visa purpose.
WaitKey = tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]


class QueueEntryOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    queued_on: date
    letter_on: date | None
    removed_on: date | None
    removed_reason: str | None
    status: QueueStatus
    days_waiting: int


class AttemptOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    number: int
    sent_on: date
    deadline_on: date
    outcome: AttemptOutcome | None
    closed_on: date | None
    status: AttemptStatus
    # Days to the deadline of an open attempt (0 on the last day).
    days_left: int | None


def entry_out(entry: WaitingListEntry, stage: CaseStage, today: date) -> QueueEntryOut:
    return QueueEntryOut(
        id=entry.id,
        case_id=entry.case_id,
        queued_on=entry.queued_on,
        letter_on=entry.letter_on,
        removed_on=entry.removed_on,
        removed_reason=entry.removed_reason,
        status=queue_status(
            letter_on=entry.letter_on, removed_on=entry.removed_on, case_stage=stage
        ),
        days_waiting=days_waiting(
            entry.queued_on, letter_on=entry.letter_on, removed_on=entry.removed_on, today=today
        ),
    )


def attempt_out(attempt: ApplicationAttempt, today: date) -> AttemptOut:
    status = attempt_status(attempt.outcome, attempt.deadline_on, today)
    return AttemptOut(
        id=attempt.id,
        case_id=attempt.case_id,
        number=attempt.number,
        sent_on=attempt.sent_on,
        deadline_on=attempt.deadline_on,
        outcome=attempt.outcome,
        closed_on=attempt.closed_on,
        status=status,
        days_left=(attempt.deadline_on - today).days if status == AttemptStatus.OPEN else None,
    )


async def appointments_of(
    session: AsyncSession, cases: list[VisaCase], today: date
) -> dict[uuid.UUID, tuple[list[QueueEntryOut], list[AttemptOut]]]:
    """Queue entries and attempts of each case, newest first."""
    ids = [c.id for c in cases]
    stages = {c.id: c.stage for c in cases}
    result: dict[uuid.UUID, tuple[list[QueueEntryOut], list[AttemptOut]]] = {
        i: ([], []) for i in ids
    }
    if not ids:
        return result
    for entry in await session.scalars(
        select(WaitingListEntry)
        .where(WaitingListEntry.case_id.in_(ids))
        .order_by(WaitingListEntry.queued_on.desc(), WaitingListEntry.created_at.desc())
    ):
        result[entry.case_id][0].append(entry_out(entry, stages[entry.case_id], today))
    for attempt in await session.scalars(
        select(ApplicationAttempt)
        .where(ApplicationAttempt.case_id.in_(ids))
        .order_by(ApplicationAttempt.number.desc())
    ):
        result[attempt.case_id][1].append(attempt_out(attempt, today))
    return result


async def average_waits(session: AsyncSession) -> dict[WaitKey, tuple[int, int]]:
    """Average days from the queue to the letter, and how many waits it is over, by
    country, city and purpose — over this workspace's clients who got a date."""
    rows = await session.execute(
        select(
            VisaCase.destination_id,
            VisaCase.visa_center_id,
            VisaCase.purpose_id,
            func.avg(WaitingListEntry.letter_on - WaitingListEntry.queued_on),
            func.count(),
        )
        .select_from(WaitingListEntry)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(WaitingListEntry.letter_on.is_not(None))
        .group_by(VisaCase.destination_id, VisaCase.visa_center_id, VisaCase.purpose_id)
    )
    return {
        (destination, center, purpose): (round(avg), count)
        for destination, center, purpose, avg, count in rows.tuples()
    }

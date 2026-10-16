"""Queues and application attempts (backlog 1.6): the waiting list entries and the
windowed applications of cases, and the «Очередь: страна» view.

Closed by the «clients» and «cases» modules. A date that came with a letter or an
attempt is the case's submission date, which moves the case on (app/core/cases.py).
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.booking import (
    AttemptOut,
    QueueEntryOut,
    attempt_out,
    average_waits,
    entry_out,
)
from app.api.cases import Member, _audit, _case
from app.api.deps import TenantSession, require_module, workspace_today
from app.core.cases import advance
from app.core.queues import SUBMITTED_STAGES, QueueStatus, deadline
from app.models import (
    ApplicationAttempt,
    AttemptOutcome,
    AuditAction,
    BookingType,
    CaseStage,
    Contact,
    CountryRule,
    EmailOwner,
    VisaCase,
    WaitingListEntry,
)
from app.models.dictionaries import Hall

router = APIRouter(
    tags=["queues"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)


# --- Schemas -------------------------------------------------------------------------


class QueueIn(BaseModel):
    queued_on: date
    # Put the whole submission group in the queue on the same day.
    with_group: bool = False


class QueueUpdate(BaseModel):
    """Only the fields sent are changed. `appointment_on` — the date in the letter — is
    saved as the case's submission date."""

    queued_on: date | None = None
    letter_on: date | None = None
    appointment_on: date | None = None
    removed_on: date | None = None
    removed_reason: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("removed_reason")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class AttemptIn(BaseModel):
    sent_on: date


class AttemptUpdate(BaseModel):
    outcome: AttemptOutcome | None = None
    closed_on: date | None = None
    appointment_on: date | None = None


class QueueRow(QueueEntryOut):
    """A line of «Очередь: страна»."""

    number: int
    contact_id: uuid.UUID
    full_name: str
    vac_number: str | None
    application_email: str | None
    application_email_owner: EmailOwner | None
    visa_center_id: uuid.UUID | None
    purpose_id: uuid.UUID | None
    registrar_id: uuid.UUID | None
    hall: Hall | None
    stage: CaseStage
    submission_on: date | None


class AttemptRow(AttemptOut):
    number_of_case: int
    contact_id: uuid.UUID
    full_name: str
    vac_number: str | None
    visa_center_id: uuid.UUID | None
    purpose_id: uuid.UUID | None
    stage: CaseStage
    submission_on: date | None


class WaitAverage(BaseModel):
    visa_center_id: uuid.UUID | None
    purpose_id: uuid.UUID | None
    average_days: int
    entries: int


class QueueView(BaseModel):
    entries: list[QueueRow]
    attempts: list[AttemptRow]
    averages: list[WaitAverage]


class QueueCountry(BaseModel):
    country_id: uuid.UUID
    waiting: int
    dated: int
    open_attempts: int


# --- Helpers -------------------------------------------------------------------------


async def _entry(session: AsyncSession, entry_id: uuid.UUID) -> WaitingListEntry:
    entry = await session.get(WaitingListEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return entry


async def _attempt(session: AsyncSession, attempt_id: uuid.UUID) -> ApplicationAttempt:
    attempt = await session.get(ApplicationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return attempt


def _set_appointment(case: VisaCase, appointment_on: date | None) -> None:
    """The date from the letter is the submission date; it moves a waiting case on."""
    if appointment_on is not None and appointment_on != case.submission_on:
        case.submission_on = appointment_on
        case.stage = advance(case.stage, {"submission_on": appointment_on})


async def _flush_or_conflict(session: AsyncSession, code: str) -> None:
    """A second open entry or attempt for a case is refused by a unique index."""
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, code) from error


# --- Waiting list --------------------------------------------------------------------


@router.post("/cases/{case_id}/queue", status_code=status.HTTP_201_CREATED)
async def put_in_queue(
    case_id: uuid.UUID, body: QueueIn, user: Member, session: TenantSession
) -> list[QueueEntryOut]:
    """Put a case — or its whole group — in the waiting list. Group members already in
    the queue are left as they are."""
    case = await _case(session, case_id)
    cases = [case]
    if body.with_group and case.group_id is not None:
        cases = list(
            await session.scalars(
                select(VisaCase).where(VisaCase.group_id == case.group_id).order_by(VisaCase.number)
            )
        )
    open_ids = set(
        await session.scalars(
            select(WaitingListEntry.case_id).where(
                WaitingListEntry.case_id.in_([c.id for c in cases]),
                WaitingListEntry.removed_on.is_(None),
            )
        )
    )
    if case.id in open_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_queued")
    entries = [
        WaitingListEntry(
            tenant_id=user.tenant_id, case_id=c.id, queued_on=body.queued_on, created_by=user.id
        )
        for c in cases
        if c.id not in open_ids
    ]
    session.add_all(entries)
    await _flush_or_conflict(session, "already_queued")
    stages = {c.id: c.stage for c in cases}
    for entry in entries:
        member = next(c for c in cases if c.id == entry.case_id)
        _audit(session, AuditAction.CASE_QUEUED, user, member, {"entry": str(entry.id)})
    await session.commit()
    today = await workspace_today(session, user.tenant_id)
    for entry in entries:
        await session.refresh(entry)
    return [entry_out(e, stages[e.case_id], today) for e in entries]


@router.patch("/queue-entries/{entry_id}")
async def update_queue_entry(
    entry_id: uuid.UUID, body: QueueUpdate, user: Member, session: TenantSession
) -> QueueEntryOut:
    entry = await _entry(session, entry_id)
    case = await _case(session, entry.case_id)
    changes = body.model_dump(exclude_unset=True)
    appointment_on = changes.pop("appointment_on", None)
    if changes.get("queued_on", ...) is None:
        changes.pop("queued_on", None)  # required: null means «leave as is»
    if "removed_on" in changes and changes["removed_on"] is None:
        changes["removed_reason"] = None
    changed = sorted(f for f, v in changes.items() if getattr(entry, f) != v)
    for field, value in changes.items():
        setattr(entry, field, value)
    if (
        (entry.letter_on is not None and entry.letter_on < entry.queued_on)
        or (entry.removed_on is not None and entry.removed_on < entry.queued_on)
        or (entry.removed_reason is not None and entry.removed_on is None)
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation")
    if appointment_on is not None:
        if appointment_on != case.submission_on:
            changed.append("appointment_on")
        _set_appointment(case, appointment_on)
    await _flush_or_conflict(session, "already_queued")
    if changed:
        _audit(
            session,
            AuditAction.CASE_QUEUE_UPDATED,
            user,
            case,
            {"entry": str(entry.id), "fields": ",".join(changed)},
        )
    await session.commit()
    await session.refresh(entry)
    await session.refresh(case)
    return entry_out(entry, case.stage, await workspace_today(session, user.tenant_id))


# --- Application attempts (windowed) -------------------------------------------------


@router.post("/cases/{case_id}/attempts", status_code=status.HTTP_201_CREATED)
async def send_attempt(
    case_id: uuid.UUID, body: AttemptIn, user: Member, session: TenantSession
) -> AttemptOut:
    """Send an application (again). The window comes from the country rule; an open
    attempt past its deadline is closed as expired, an open one within it is refused."""
    case = await _case(session, case_id)
    window = await session.scalar(
        select(CountryRule.window_days).where(
            CountryRule.destination_id == case.destination_id,
            CountryRule.submission_id == case.submission_country_id,
            CountryRule.booking_type == BookingType.WINDOWED_APPLICATION,
        )
    )
    if window is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_window")
    today = await workspace_today(session, user.tenant_id)
    current = await session.scalar(
        select(ApplicationAttempt).where(
            ApplicationAttempt.case_id == case.id, ApplicationAttempt.outcome.is_(None)
        )
    )
    if current is not None:
        if today <= current.deadline_on:
            raise HTTPException(status.HTTP_409_CONFLICT, "attempt_open")
        current.outcome = AttemptOutcome.EXPIRED
        current.closed_on = current.deadline_on
        await session.flush()
    last = await session.scalar(
        select(func.max(ApplicationAttempt.number)).where(ApplicationAttempt.case_id == case.id)
    )
    attempt = ApplicationAttempt(
        tenant_id=user.tenant_id,
        case_id=case.id,
        number=(last or 0) + 1,
        sent_on=body.sent_on,
        deadline_on=deadline(body.sent_on, window),
        created_by=user.id,
    )
    session.add(attempt)
    await _flush_or_conflict(session, "attempt_open")
    _audit(session, AuditAction.CASE_ATTEMPT_SENT, user, case, {"attempt": str(attempt.id)})
    await session.commit()
    await session.refresh(attempt)
    return attempt_out(attempt, today)


@router.patch("/attempts/{attempt_id}")
async def update_attempt(
    attempt_id: uuid.UUID, body: AttemptUpdate, user: Member, session: TenantSession
) -> AttemptOut:
    """Close an attempt: a date came (with the appointment), it expired or was cancelled.
    Clearing the outcome reopens it."""
    attempt = await _attempt(session, attempt_id)
    case = await _case(session, attempt.case_id)
    today = await workspace_today(session, user.tenant_id)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    appointment_on = changes.pop("appointment_on", None)
    if "outcome" in changes:
        if changes["outcome"] is None:
            changes["closed_on"] = None
        elif changes.get("closed_on") is None:
            changes["closed_on"] = attempt.closed_on or today
    changed = sorted(f for f, v in changes.items() if getattr(attempt, f) != v)
    for field, value in changes.items():
        setattr(attempt, field, value)
    if attempt.closed_on is not None and attempt.outcome is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation")
    if appointment_on is not None:
        if appointment_on != case.submission_on:
            changed.append("appointment_on")
        _set_appointment(case, appointment_on)
    await _flush_or_conflict(session, "attempt_open")
    if changed:
        _audit(
            session,
            AuditAction.CASE_ATTEMPT_UPDATED,
            user,
            case,
            {"attempt": str(attempt.id), "fields": ",".join(changed)},
        )
    await session.commit()
    await session.refresh(attempt)
    return attempt_out(attempt, today)


# --- «Очередь: страна» ---------------------------------------------------------------


@router.get("/queues/summary")
async def queue_summary(user: Member, session: TenantSession) -> list[QueueCountry]:
    """Countries with clients in a queue or with an application: how many wait, how many
    have a date, how many applications are open."""
    entries = await session.execute(
        select(
            VisaCase.destination_id,
            func.count().filter(WaitingListEntry.letter_on.is_(None)),
            func.count().filter(WaitingListEntry.letter_on.is_not(None)),
        )
        .select_from(WaitingListEntry)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(WaitingListEntry.removed_on.is_(None), VisaCase.stage.not_in(SUBMITTED_STAGES))
        .group_by(VisaCase.destination_id)
    )
    attempts = await session.execute(
        select(VisaCase.destination_id, func.count())
        .select_from(ApplicationAttempt)
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .where(ApplicationAttempt.outcome.is_(None))
        .group_by(VisaCase.destination_id)
    )
    summary: dict[uuid.UUID, QueueCountry] = {}
    for country, waiting, dated in entries.tuples():
        summary[country] = QueueCountry(
            country_id=country, waiting=waiting, dated=dated, open_attempts=0
        )
    for country, count in attempts.tuples():
        row = summary.setdefault(
            country, QueueCountry(country_id=country, waiting=0, dated=0, open_attempts=0)
        )
        row.open_attempts = count
    return sorted(summary.values(), key=lambda r: -(r.waiting + r.dated + r.open_attempts))


@router.get("/queues")
async def queue_view(
    user: Member,
    session: TenantSession,
    country_id: Annotated[uuid.UUID, Query()],
    closed: Annotated[bool, Query()] = False,
) -> QueueView:
    """«Очередь: страна»: who waits since when under what number, the applications with
    their deadlines, and the average wait by city and purpose. Without `closed`, only
    those still waiting or with a date not yet submitted."""
    today = await workspace_today(session, user.tenant_id)
    query = (
        select(WaitingListEntry, VisaCase, Contact.full_name)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(VisaCase.destination_id == country_id)
        .order_by(WaitingListEntry.queued_on, VisaCase.number)
    )
    if not closed:
        query = query.where(
            WaitingListEntry.removed_on.is_(None), VisaCase.stage.not_in(SUBMITTED_STAGES)
        )
    entries = []
    for entry, case, name in (await session.execute(query)).tuples():
        out = entry_out(entry, case.stage, today)
        if not closed and out.status not in (QueueStatus.IN_QUEUE, QueueStatus.DATE_RECEIVED):
            continue
        entries.append(
            QueueRow(
                **out.model_dump(),
                number=case.number,
                contact_id=case.contact_id,
                full_name=name,
                vac_number=case.vac_number,
                application_email=case.application_email,
                application_email_owner=case.application_email_owner,
                visa_center_id=case.visa_center_id,
                purpose_id=case.purpose_id,
                registrar_id=case.registrar_id,
                hall=case.hall,
                stage=case.stage,
                submission_on=case.submission_on,
            )
        )

    # The latest attempt of each case for this country.
    latest = (
        select(ApplicationAttempt, VisaCase, Contact.full_name)
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(VisaCase.destination_id == country_id)
        .order_by(ApplicationAttempt.case_id, ApplicationAttempt.number.desc())
        .distinct(ApplicationAttempt.case_id)
    )
    if not closed:
        latest = latest.where(VisaCase.stage.not_in(SUBMITTED_STAGES))
    attempts = [
        AttemptRow(
            **attempt_out(attempt, today).model_dump(),
            number_of_case=case.number,
            contact_id=case.contact_id,
            full_name=name,
            vac_number=case.vac_number,
            visa_center_id=case.visa_center_id,
            purpose_id=case.purpose_id,
            stage=case.stage,
            submission_on=case.submission_on,
        )
        for attempt, case, name in (await session.execute(latest)).tuples()
    ]
    attempts.sort(key=lambda a: a.deadline_on)

    averages = [
        WaitAverage(visa_center_id=center, purpose_id=purpose, average_days=days, entries=count)
        for (destination, center, purpose), (days, count) in (await average_waits(session)).items()
        if destination == country_id
    ]
    return QueueView(entries=entries, attempts=attempts, averages=averages)

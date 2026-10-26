"""Cases (backlog 1.4): one visa for one applicant, submission groups, the stage and the
structured visa result. «Создать кейс» from a consultation creates a case for every
applicant and joins them in a submission group.

Closed by the «clients» and «cases» modules: cases live in the client's card. Audit
events carry ids and the names of changed fields only.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Date, cast, func, or_, select, text, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import AttemptOut, QueueEntryOut, appointments_of, average_waits
from app.api.bookings import BookingOut, bookings_of
from app.api.client_status import Tab, latest_consultation, tab_of
from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.cases import VISA_DETAILS, advance, appeal_until, cleared_by_outcome
from app.core.employee_limits import LimitKind, watch
from app.core.names import NAME_PARTS, NamePart, name_fields, tidy
from app.models import (
    AuditAction,
    BookingType,
    CaseStage,
    Consultation,
    Contact,
    ContactRelation,
    Country,
    CountryRule,
    Decision,
    EmailOwner,
    Registrar,
    SubmissionGroup,
    VacIdentifierType,
    VisaCase,
    VisaCenter,
    VisaEntries,
    VisaOutcome,
    VisaPurpose,
)
from app.models.dictionaries import Hall

router = APIRouter(
    tags=["cases"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)

Member = Annotated[CurrentUser, Depends(require_module("cases"))]

MAX_APPLICANTS = 30
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# Board (backlog 1.5): results stay in the «Результат» column for a month, leads in the
# «Консультация» column for three months after their latest consultation.
RECENT_RESULT_DAYS = 30
RECENT_LEAD_DAYS = 90
BOARD_LIMIT = 500


# --- Schemas -------------------------------------------------------------------------


class ApplicantIn(BaseModel):
    """An existing contact, or a new one by name: a family member without a card yet,
    their surname, given name and patronymic apart as on the card (the surname or the
    given name is enough). `label` says who the new person is to the card's contact
    ("дочь")."""

    contact_id: uuid.UUID | None = None
    last_name: NamePart | None = None
    first_name: NamePart | None = None
    middle_name: NamePart | None = None
    label: Annotated[str, Field(max_length=100)] | None = None

    _name_parts = field_validator(*NAME_PARTS)(tidy)

    @field_validator("label")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def one_of(self) -> "ApplicantIn":
        named = self.last_name is not None or self.first_name is not None
        if self.contact_id is not None and (named or self.middle_name is not None):
            raise ValueError("an applicant is either an existing contact or a new name")
        if self.contact_id is None and not named:
            raise ValueError("a new applicant needs the surname or the given name")
        return self


class CaseCreate(BaseModel):
    consultation_id: uuid.UUID | None = None
    destination_id: uuid.UUID
    submission_country_id: uuid.UUID
    purpose_id: uuid.UUID | None = None
    visa_center_id: uuid.UUID | None = None
    registrar_id: uuid.UUID | None = None
    hall: Hall | None = None
    submission_on: date | None = None
    applicants: Annotated[list[ApplicantIn], Field(min_length=1, max_length=MAX_APPLICANTS)]


class CaseUpdate(BaseModel):
    """Only the fields sent are changed; null clears an optional field."""

    destination_id: uuid.UUID | None = None
    submission_country_id: uuid.UUID | None = None
    purpose_id: uuid.UUID | None = None
    visa_center_id: uuid.UUID | None = None
    registrar_id: uuid.UUID | None = None
    hall: Hall | None = None
    stage: CaseStage | None = None
    submission_on: date | None = None
    outcome: VisaOutcome | None = None
    decided_on: date | None = None
    visa_entries: VisaEntries | None = None
    visa_valid_from: date | None = None
    visa_valid_to: date | None = None
    stay_days: int | None = Field(default=None, ge=1, le=3660)
    after_appeal: bool | None = None
    vac_number: Annotated[str, Field(max_length=64)] | None = None
    application_email: Annotated[str, Field(max_length=320, pattern=EMAIL)] | None = None
    application_email_owner: EmailOwner | None = None

    @field_validator("vac_number", "application_email", mode="before")
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        """Trimmed before the email pattern is checked; an empty field clears it."""
        if isinstance(value, str):
            return value.strip() or None
        return value


class GroupMember(BaseModel):
    case_id: uuid.UUID
    number: int
    contact_id: uuid.UUID
    full_name: str
    destination_id: uuid.UUID
    stage: CaseStage


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    contact_id: uuid.UUID
    group_id: uuid.UUID | None
    consultation_id: uuid.UUID | None
    destination_id: uuid.UUID
    purpose_id: uuid.UUID | None
    submission_country_id: uuid.UUID
    visa_center_id: uuid.UUID | None
    registrar_id: uuid.UUID | None
    hall: Hall | None
    stage: CaseStage
    submission_on: date | None
    outcome: VisaOutcome | None
    decided_on: date | None
    visa_entries: VisaEntries | None
    visa_valid_from: date | None
    visa_valid_to: date | None
    stay_days: int | None
    after_appeal: bool
    created_at: datetime
    # The other cases of the submission group.
    group: list[GroupMember] = []
    # From the country rules: does the consulate accept appeals (None: not known), and
    # the last day to appeal a refusal.
    appeals_accepted: bool | None = None
    appeal_until: date | None = None
    # Number and email the visa center knows the client by.
    vac_number: str | None
    application_email: str | None
    application_email_owner: EmailOwner | None
    # From the country rule: how the appointment is got and what the number is.
    booking_type: BookingType | None = None
    identifier_type: VacIdentifierType | None = None
    window_days: int | None = None
    queue_entries: list[QueueEntryOut] = []
    attempts: list[AttemptOut] = []
    # Average days from the queue to the letter for this country, city and purpose.
    average_wait_days: int | None = None
    # Hotels and the client's flight booking for the document package.
    bookings: list[BookingOut] = []


class GroupIn(BaseModel):
    case_id: uuid.UUID


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    case: VisaCase,
    details: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        action,
        actor=user,
        target_type="case",
        target_id=case.id,
        details={"contact": str(case.contact_id), **(details or {})},
    )


async def _case(session: AsyncSession, case_id: uuid.UUID) -> VisaCase:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    case = await session.get(VisaCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return case


async def _check_refs(session: AsyncSession, fields: dict[str, Any]) -> None:
    """Purposes and registrars must be this workspace's (RLS), countries must exist."""
    for field, model in (
        ("destination_id", Country),
        ("submission_country_id", Country),
        ("purpose_id", VisaPurpose),
        ("registrar_id", Registrar),
        ("visa_center_id", VisaCenter),
    ):
        if fields.get(field) is not None and await session.get(model, fields[field]) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def _check_place(
    session: AsyncSession,
    destination_id: uuid.UUID,
    submission_country_id: uuid.UUID,
    visa_center_id: uuid.UUID | None,
) -> None:
    """A visa is applied for in another country, at a center of the rule for this pair."""
    if destination_id == submission_country_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "same_country")
    if visa_center_id is None:
        return
    pair = await session.scalar(
        select(CountryRule.id)
        .join(VisaCenter, VisaCenter.rule_id == CountryRule.id)
        .where(
            VisaCenter.id == visa_center_id,
            CountryRule.destination_id == destination_id,
            CountryRule.submission_id == submission_country_id,
        )
    )
    if pair is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "visa_center_mismatch")


async def _next_numbers(session: AsyncSession, tenant_id: uuid.UUID, count: int) -> list[int]:
    """Case numbers go on per workspace (№1, №2, …). The transaction lock makes two
    cases created at the same moment wait for each other instead of taking one number."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"visa_cases:{tenant_id}"},
    )
    # Counted past the employee's view: they do not see every case (migration 0031).
    last = await session.scalar(select(func.last_case_number())) or 0
    return [last + i for i in range(1, count + 1)]


async def cases_out(session: AsyncSession, cases: list[VisaCase]) -> list[CaseOut]:
    """Cases with their group members and the appeal rules of their country pair."""
    group_ids = {c.group_id for c in cases if c.group_id is not None}
    members: dict[uuid.UUID, list[GroupMember]] = {}
    if group_ids:
        rows = await session.execute(
            select(VisaCase, Contact.full_name)
            .join(Contact, Contact.id == VisaCase.contact_id)
            .where(VisaCase.group_id.in_(group_ids))
            .order_by(VisaCase.number)
        )
        for member, name in rows.tuples():
            assert member.group_id is not None
            members.setdefault(member.group_id, []).append(
                GroupMember(
                    case_id=member.id,
                    number=member.number,
                    contact_id=member.contact_id,
                    full_name=name,
                    destination_id=member.destination_id,
                    stage=member.stage,
                )
            )
    pairs = {(c.destination_id, c.submission_country_id) for c in cases}
    rules: dict[tuple[uuid.UUID, uuid.UUID], CountryRule] = {}
    if pairs:
        for rule in await session.scalars(
            select(CountryRule).where(
                tuple_(CountryRule.destination_id, CountryRule.submission_id).in_(list(pairs))
            )
        ):
            rules[(rule.destination_id, rule.submission_id)] = rule

    today = await workspace_today(session, cases[0].tenant_id) if cases else None
    appointments = await appointments_of(session, cases, today) if today else {}
    bookings = await bookings_of(session, cases, today) if today else {}
    waits = await average_waits(session) if cases else {}

    result = []
    for case in cases:
        out = CaseOut.model_validate(case)
        if case.group_id is not None:
            out.group = [m for m in members[case.group_id] if m.case_id != case.id]
        out.queue_entries, out.attempts = appointments.get(case.id, ([], []))
        out.bookings = bookings.get(case.id, [])
        wait = waits.get((case.destination_id, case.visa_center_id, case.purpose_id))
        out.average_wait_days = wait[0] if wait else None
        rule = rules.get((case.destination_id, case.submission_country_id))
        if rule is not None:
            out.booking_type = rule.booking_type
            out.identifier_type = rule.identifier_type
            out.window_days = rule.window_days
            out.appeals_accepted = rule.appeals_accepted
            out.appeal_until = appeal_until(
                case.outcome,
                case.decided_on,
                case.after_appeal,
                rule.appeal_deadline_days if rule.appeals_accepted else None,
            )
        result.append(out)
    return result


async def cases_of(session: AsyncSession, contact_id: uuid.UUID) -> list[CaseOut]:
    """A contact's cases for the card, newest first."""
    cases = list(
        await session.scalars(
            select(VisaCase)
            .where(VisaCase.contact_id == contact_id)
            .order_by(VisaCase.number.desc())
        )
    )
    return await cases_out(session, cases)


async def _one_out(session: AsyncSession, case: VisaCase) -> CaseOut:
    await session.refresh(case)
    return (await cases_out(session, [case]))[0]


async def _new_contact(
    session: AsyncSession, user: CurrentUser, anchor: Contact, applicant: ApplicantIn
) -> Contact:
    """A family member gets their own card, linked to the card the case was started from.
    They came the same way, so the channel and the partner are copied."""
    contact = Contact(
        tenant_id=user.tenant_id,
        **name_fields(applicant.last_name, applicant.first_name, applicant.middle_name),
        lead_channel_id=anchor.lead_channel_id,
        partner_id=anchor.partner_id,
        created_by=user.id,
    )
    session.add(contact)
    await session.flush()
    session.add_all(
        [
            ContactRelation(
                tenant_id=user.tenant_id,
                contact_id=anchor.id,
                related_id=contact.id,
                label=applicant.label,
            ),
            ContactRelation(tenant_id=user.tenant_id, contact_id=contact.id, related_id=anchor.id),
        ]
    )
    record(
        session,
        AuditAction.CONTACT_CREATED,
        actor=user,
        target_type="contact",
        target_id=contact.id,
    )
    record(
        session,
        AuditAction.CONTACT_LINKED,
        actor=user,
        target_type="contact",
        target_id=anchor.id,
        details={"related": str(contact.id)},
    )
    return contact


# --- Endpoints -----------------------------------------------------------------------


@router.post("/contacts/{contact_id}/cases", status_code=status.HTTP_201_CREATED)
async def create_cases(
    contact_id: uuid.UUID, body: CaseCreate, user: Member, session: TenantSession
) -> list[CaseOut]:
    """One case per applicant; two or more applicants form a submission group. From a
    consultation the decision becomes «yes»."""
    anchor = await session.get(Contact, contact_id)
    if anchor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    consultation: Consultation | None = None
    if body.consultation_id is not None:
        consultation = await session.get(Consultation, body.consultation_id)
        if consultation is None or consultation.contact_id != anchor.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    fields = body.model_dump(exclude={"applicants", "consultation_id"})
    await _check_refs(session, fields)
    await _check_place(
        session, body.destination_id, body.submission_country_id, body.visa_center_id
    )
    existing = [a.contact_id for a in body.applicants if a.contact_id is not None]
    if len(existing) != len(set(existing)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_applicant")
    if existing:
        found = set(await session.scalars(select(Contact.id).where(Contact.id.in_(existing))))
        if set(existing) - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

    applicant_ids = [
        a.contact_id
        if a.contact_id is not None
        else (await _new_contact(session, user, anchor, a)).id
        for a in body.applicants
    ]
    group: SubmissionGroup | None = None
    if len(applicant_ids) > 1:
        group = SubmissionGroup(tenant_id=user.tenant_id)
        session.add(group)
        await session.flush()
    numbers = await _next_numbers(session, user.tenant_id, len(applicant_ids))
    stage = advance(CaseStage.WAITING, fields)
    cases = [
        VisaCase(
            tenant_id=user.tenant_id,
            number=number,
            contact_id=applicant_id,
            group_id=group.id if group else None,
            consultation_id=consultation.id if consultation else None,
            stage=stage,
            created_by=user.id,
            **fields,
        )
        for number, applicant_id in zip(numbers, applicant_ids, strict=True)
    ]
    session.add_all(cases)
    await session.flush()
    for case in cases:
        _audit(session, AuditAction.CASE_CREATED, user, case)
    if consultation is not None and consultation.decision != Decision.YES:
        consultation.decision = Decision.YES
        record(
            session,
            AuditAction.CONSULTATION_UPDATED,
            actor=user,
            target_type="consultation",
            target_id=consultation.id,
            details={"contact": str(anchor.id), "fields": "decision"},
        )
    await session.commit()
    for case in cases:
        await session.refresh(case)
    return await cases_out(session, cases)


# --- Board (client index, backlog 1.5) ------------------------------------------------


class BoardCase(BaseModel):
    id: uuid.UUID
    number: int
    contact_id: uuid.UUID
    full_name: str
    group_id: uuid.UUID | None
    destination_id: uuid.UUID
    purpose_id: uuid.UUID | None
    registrar_id: uuid.UUID | None
    stage: CaseStage
    outcome: VisaOutcome | None
    submission_on: date | None
    decided_on: date | None


class BoardLead(BaseModel):
    """A client in the «Консультация» column: no case in work yet."""

    contact_id: uuid.UUID
    full_name: str
    tab: Tab
    held_on: date | None
    decision: Decision | None
    destination_ids: list[uuid.UUID]


class Board(BaseModel):
    cases: list[BoardCase]
    leads: list[BoardLead]


class StageIn(BaseModel):
    """Drag and drop on the board: a case, or a submission group moved together."""

    case_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=MAX_APPLICANTS)]
    stage: CaseStage


@router.get("/cases/board")
async def board(
    user: Member,
    session: TenantSession,
    country_id: Annotated[uuid.UUID | None, Query()] = None,
    channel_id: Annotated[uuid.UUID | None, Query()] = None,
    registrar_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Board:
    """Kanban of the client index: cases in work by stage, results of the last month, and
    clients after a consultation without a case in work."""
    today = await workspace_today(session, user.tenant_id)
    decided = func.coalesce(VisaCase.decided_on, cast(VisaCase.updated_at, Date))
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            or_(
                VisaCase.stage != CaseStage.RESULT,
                decided >= today - timedelta(days=RECENT_RESULT_DAYS),
            )
        )
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    if channel_id is not None:
        query = query.where(Contact.lead_channel_id == channel_id)
    if registrar_id is not None:
        query = query.where(VisaCase.registrar_id == registrar_id)
    rows = await session.execute(query.order_by(VisaCase.number.desc()).limit(BOARD_LIMIT))
    cases = [
        BoardCase(
            id=c.id,
            number=c.number,
            contact_id=c.contact_id,
            full_name=name,
            group_id=c.group_id,
            destination_id=c.destination_id,
            purpose_id=c.purpose_id,
            registrar_id=c.registrar_id,
            stage=c.stage,
            outcome=c.outcome,
            submission_on=c.submission_on,
            decided_on=c.decided_on,
        )
        for c, name in rows.tuples()
    ]

    leads: list[BoardLead] = []
    if registrar_id is None:  # leads have no registrar yet
        latest = latest_consultation()
        tab = tab_of(latest)
        since = func.coalesce(latest.c.held_on, cast(Contact.created_at, Date))
        lead_query = (
            select(Contact.id, Contact.full_name, tab, latest.c.held_on, latest.c.decision)
            .add_columns(latest.c.destination_ids)
            .outerjoin(latest, true())
            .where(
                tab.in_([Tab.CONSULT.value, Tab.THINKING.value]),
                since >= today - timedelta(days=RECENT_LEAD_DAYS),
            )
        )
        if country_id is not None:
            lead_query = lead_query.where(latest.c.destination_ids.any(country_id))
        if channel_id is not None:
            lead_query = lead_query.where(Contact.lead_channel_id == channel_id)
        for contact_id, name, lead_tab, held_on, decision, destinations in (
            await session.execute(lead_query.order_by(since.desc()).limit(BOARD_LIMIT))
        ).tuples():
            leads.append(
                BoardLead(
                    contact_id=contact_id,
                    full_name=name,
                    tab=Tab(lead_tab),
                    held_on=held_on,
                    decision=decision,
                    destination_ids=destinations or [],
                )
            )
    return Board(cases=cases, leads=leads)


@router.post("/cases/stage", status_code=status.HTTP_204_NO_CONTENT)
async def move_cases(body: StageIn, user: Member, session: TenantSession) -> Response:
    """Move cases to a stage by hand (the board's drag and drop)."""
    ids = set(body.case_ids)
    cases = list(await session.scalars(select(VisaCase).where(VisaCase.id.in_(ids))))
    if len(cases) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    for case in cases:
        if case.stage != body.stage:
            case.stage = body.stage
            _audit(session, AuditAction.CASE_UPDATED, user, case, {"fields": "stage"})
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, user: Member, session: TenantSession) -> CaseOut:
    case = await _case(session, case_id)
    # An employee's open counts against their daily limit and goes to the log (Т.14).
    await watch(session, user, LimitKind.CARDS, "case", case.id, AuditAction.CASE_VIEWED)
    return await _one_out(session, case)


@router.patch("/cases/{case_id}")
async def update_case(
    case_id: uuid.UUID, body: CaseUpdate, user: Member, session: TenantSession
) -> CaseOut:
    case = await _case(session, case_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("destination_id", "submission_country_id", "stage", "after_appeal"):
        if changes.get(required, ...) is None:
            changes.pop(required)  # null means «leave as is» for required fields
    await _check_refs(session, changes)
    if "outcome" in changes:
        changes = {**cleared_by_outcome(changes["outcome"]), **changes}
    if "stage" not in changes:
        # Only a new date or result moves the case: a form that sends the saved values
        # again leaves a case the specialist moved back by hand where it is.
        new = {f: v for f, v in changes.items() if getattr(case, f) != v}
        changes["stage"] = advance(case.stage, new)
    changed = sorted(f for f, v in changes.items() if getattr(case, f) != v)
    for field, value in changes.items():
        setattr(case, field, value)
    await _check_place(
        session, case.destination_id, case.submission_country_id, case.visa_center_id
    )
    invalid_result = (
        (
            case.outcome != VisaOutcome.VISA
            and any(getattr(case, f) is not None for f in VISA_DETAILS)
        )
        or (case.decided_on is not None and case.outcome is None)
        or (case.after_appeal and case.outcome not in (VisaOutcome.VISA, VisaOutcome.REFUSAL))
        or (
            case.visa_valid_from is not None
            and case.visa_valid_to is not None
            and case.visa_valid_to < case.visa_valid_from
        )
    )
    if invalid_result:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation")
    if changed:
        _audit(session, AuditAction.CASE_UPDATED, user, case, {"fields": ",".join(changed)})
    await session.commit()
    return await _one_out(session, case)


# --- Submission groups ---------------------------------------------------------------


@router.get("/cases/{case_id}/group-candidates")
async def group_candidates(case_id: uuid.UUID, session: TenantSession) -> list[GroupMember]:
    """Cases of the contact's family and links that can join this case's group."""
    case = await _case(session, case_id)
    related = select(ContactRelation.related_id).where(
        ContactRelation.contact_id == case.contact_id
    )
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(VisaCase.contact_id.in_(related))
        .order_by(VisaCase.number.desc())
    )
    if case.group_id is not None:
        query = query.where((VisaCase.group_id.is_(None)) | (VisaCase.group_id != case.group_id))
    rows = await session.execute(query)
    return [
        GroupMember(
            case_id=c.id,
            number=c.number,
            contact_id=c.contact_id,
            full_name=name,
            destination_id=c.destination_id,
            stage=c.stage,
        )
        for c, name in rows.tuples()
    ]


@router.post("/cases/{case_id}/group")
async def join_group(
    case_id: uuid.UUID, body: GroupIn, user: Member, session: TenantSession
) -> CaseOut:
    """Put two cases in one submission group. A case already in another group leaves it
    first (the specialist does it explicitly), so groups are never merged by accident."""
    if body.case_id == case_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "self_link")
    case = await _case(session, case_id)
    other = await _case(session, body.case_id)
    if case.group_id is not None and case.group_id == other.group_id:
        return await _one_out(session, case)
    if case.group_id is not None and other.group_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "in_another_group")
    group_id = case.group_id or other.group_id
    if group_id is None:
        group = SubmissionGroup(tenant_id=user.tenant_id)
        session.add(group)
        await session.flush()
        group_id = group.id
    for member in (case, other):
        if member.group_id != group_id:
            member.group_id = group_id
            _audit(session, AuditAction.CASE_GROUPED, user, member, {"group": str(group_id)})
    await session.commit()
    return await _one_out(session, case)


@router.delete("/cases/{case_id}/group", status_code=status.HTTP_204_NO_CONTENT)
async def leave_group(case_id: uuid.UUID, user: Member, session: TenantSession) -> Response:
    """The case leaves its group; a group left with one case is dissolved."""
    case = await _case(session, case_id)
    if case.group_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    group_id = case.group_id
    case.group_id = None
    _audit(session, AuditAction.CASE_UNGROUPED, user, case, {"group": str(group_id)})
    await session.flush()
    remaining = list(await session.scalars(select(VisaCase).where(VisaCase.group_id == group_id)))
    if len(remaining) <= 1:
        for member in remaining:
            member.group_id = None
            _audit(session, AuditAction.CASE_UNGROUPED, user, member, {"group": str(group_id)})
        await session.flush()
        group = await session.get(SubmissionGroup, group_id)
        if group is not None:
            await session.delete(group)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import (
    Date,
    and_,
    any_,
    cast,
    delete,
    func,
    or_,
    select,
    text,
    true,
    tuple_,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import AttemptOut, QueueEntryOut, appointments_of, average_waits
from app.api.bookings import BookingOut, bookings_of
from app.api.client_status import Tab, latest_consultation, tab_of
from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.cases import (
    PLACE_FIELDS,
    RESULT_FIELDS,
    VISA_DETAILS,
    ResultPeriod,
    advance,
    appeal_until,
    by_dates,
    cleared_by_outcome,
    for_group_member,
    result_window,
)
from app.core.days import Day
from app.core.employee_limits import LimitKind, watch
from app.core.names import NAME_PARTS, NamePart, name_fields, tidy
from app.models import (
    ApplicationAttempt,
    AuditAction,
    Booking,
    BookingType,
    CaseExpense,
    CaseFinance,
    CasePayment,
    CaseStage,
    Consultation,
    Contact,
    ContactRelation,
    Country,
    CountryRule,
    Decision,
    EmailOwner,
    EmployeeCase,
    PackageItem,
    Registrar,
    SubmissionGroup,
    Tenant,
    UserRole,
    VacIdentifierType,
    VisaCase,
    VisaCenter,
    VisaEntries,
    VisaOutcome,
    VisaPurpose,
    WaitingListEntry,
)
from app.models.dictionaries import Hall

router = APIRouter(
    tags=["cases"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)

Member = Annotated[CurrentUser, Depends(require_module("cases"))]

MAX_APPLICANTS = 30
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# Board (backlog 1.5): the «Результат» column shows the decisions of the period the
# specialist picks (app/core/cases.py → result_window, a month by default), the
# «Консультация» column leads of three months after their latest consultation.
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
    # Filing again: the case this one repeats. It belongs to one of the applicants, whose
    # new case is linked to it.
    previous_case_id: uuid.UUID | None = None
    destination_id: uuid.UUID
    submission_country_id: uuid.UUID
    purpose_id: uuid.UUID | None = None
    visa_center_id: uuid.UUID | None = None
    registrar_id: uuid.UUID | None = None
    hall: Hall | None = None
    submission_on: Day | None = None
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
    submission_on: Day | None = None
    questionnaire_due_on: Day | None = None
    questionnaire_received_on: Day | None = None
    outcome: VisaOutcome | None = None
    decided_on: Day | None = None
    visa_entries: VisaEntries | None = None
    visa_valid_from: Day | None = None
    visa_valid_to: Day | None = None
    stay_days: int | None = Field(default=None, ge=1, le=3660)
    after_appeal: bool | None = None
    vac_number: Annotated[str, Field(max_length=64)] | None = None
    application_email: Annotated[str, Field(max_length=320, pattern=EMAIL)] | None = None
    application_email_owner: EmailOwner | None = None
    # Fill the number and the email in for the rest of the submission group where they
    # are empty (owner's decision 2026-11-04); a member's own value is kept.
    fill_group: bool = False
    # The same change for the rest of the group, apart from the result (owner's decision
    # 2026-11-15, app/core/cases.py → for_group_member).
    apply_to_group: bool = False
    # The result entered or changed here goes into the group's cases without a result
    # (pilot fix 7, owner's decision 2026-11-19); cases with a result are left alone.
    result_to_group: bool = False

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
    # The member's own result: the case form offers the same result only to those
    # without one (pilot fix 7).
    outcome: VisaOutcome | None = None


class CaseSource(BaseModel):
    """The consultation a case was opened from and whose it was (pilot fix 2)."""

    consultation_id: uuid.UUID
    held_on: date
    contact_id: uuid.UUID
    full_name: str


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    contact_id: uuid.UUID
    group_id: uuid.UUID | None
    consultation_id: uuid.UUID | None
    previous_case_id: uuid.UUID | None
    destination_id: uuid.UUID
    purpose_id: uuid.UUID | None
    submission_country_id: uuid.UUID
    visa_center_id: uuid.UUID | None
    registrar_id: uuid.UUID | None
    hall: Hall | None
    stage: CaseStage
    submission_on: date | None
    questionnaire_due_on: date | None
    questionnaire_received_on: date | None
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
    # Its consultation, when the user sees whose it was.
    source: CaseSource | None = None
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


async def _open_card_of(session: AsyncSession, user: CurrentUser, case: VisaCase) -> None:
    """An answer with the case opens its client's card for an employee's daily limit
    (Т.14): the card lists the case, so from the card it costs nothing more."""
    await watch(
        session, user, LimitKind.CARDS, "contact", case.contact_id, AuditAction.CONTACT_VIEWED
    )


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
    # Counted past the employee's view: they do not see every case (migration 0031), and
    # past the deleted ones (migration 0038).
    last = max(
        await session.scalar(select(func.last_case_number())) or 0,
        await session.scalar(select(Tenant.max_deleted_case_number).where(Tenant.id == tenant_id))
        or 0,
    )
    return [last + i for i in range(1, count + 1)]


async def sources_of(session: AsyncSession, cases: list[VisaCase]) -> dict[uuid.UUID, CaseSource]:
    """The consultation each case was opened from, with whose it was (pilot fix 2). RLS
    hides a consultation and a card an employee does not see: then there is no source,
    so a name never reaches them through the link."""
    ids = {c.consultation_id for c in cases if c.consultation_id is not None}
    if not ids:
        return {}
    found = {
        consultation_id: CaseSource(
            consultation_id=consultation_id, held_on=held_on, contact_id=contact_id, full_name=name
        )
        for consultation_id, held_on, contact_id, name in await session.execute(
            select(
                Consultation.id, Consultation.held_on, Consultation.contact_id, Contact.full_name
            )
            .join(Contact, Contact.id == Consultation.contact_id)
            .where(Consultation.id.in_(ids))
        )
    }
    return {
        c.id: found[c.consultation_id]
        for c in cases
        if c.consultation_id is not None and c.consultation_id in found
    }


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
        for member, name in rows:
            assert member.group_id is not None
            members.setdefault(member.group_id, []).append(
                GroupMember(
                    case_id=member.id,
                    number=member.number,
                    contact_id=member.contact_id,
                    full_name=name,
                    destination_id=member.destination_id,
                    stage=member.stage,
                    outcome=member.outcome,
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
    sources = await sources_of(session, cases)

    result = []
    for case in cases:
        out = CaseOut.model_validate(case)
        if case.group_id is not None:
            out.group = [m for m in members[case.group_id] if m.case_id != case.id]
        out.queue_entries, out.attempts = appointments.get(case.id, ([], []))
        out.bookings = bookings.get(case.id, [])
        out.source = sources.get(case.id)
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


async def _cases_in_work(
    session: AsyncSession, contact_ids: list[uuid.UUID]
) -> JSONResponse | None:
    """409 when an applicant already has a case in work: a second one is most likely a
    duplicate (pilot fix 8), so the specialist confirms it. Only the cases the user sees."""
    rows = (
        await session.execute(
            select(VisaCase.contact_id, Contact.full_name, VisaCase.number, VisaCase.destination_id)
            .join(Contact, Contact.id == VisaCase.contact_id)
            .where(VisaCase.contact_id.in_(contact_ids), VisaCase.stage != CaseStage.RESULT)
            .order_by(VisaCase.number)
        )
    ).all()
    if not rows:
        return None
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "case_in_work",
            "cases": [
                {
                    "contact_id": str(contact_id),
                    "full_name": name,
                    "number": number,
                    "destination_id": str(destination_id),
                }
                for contact_id, name, number, destination_id in rows
            ],
        },
    )


# --- Endpoints -----------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=list[CaseOut],
)
async def create_cases(
    contact_id: uuid.UUID,
    body: CaseCreate,
    user: Member,
    session: TenantSession,
    allow_case_in_work: bool = False,
) -> Any:
    """One case per applicant; two or more applicants form a submission group. From a
    consultation the decision becomes «yes». An applicant who already has a case in work
    is asked about first (409 `case_in_work` with those cases; pilot fix 8): the form
    confirms and sends `allow_case_in_work`."""
    anchor = await session.get(Contact, contact_id)
    if anchor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    consultation: Consultation | None = None
    if body.consultation_id is not None:
        consultation = await session.get(Consultation, body.consultation_id)
        if consultation is None or consultation.contact_id != anchor.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    fields = body.model_dump(exclude={"applicants", "consultation_id", "previous_case_id"})
    await _check_refs(session, fields)
    await _check_place(
        session, body.destination_id, body.submission_country_id, body.visa_center_id
    )
    existing = [a.contact_id for a in body.applicants if a.contact_id is not None]
    if len(existing) != len(set(existing)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_applicant")
    previous: VisaCase | None = None
    if body.previous_case_id is not None:
        previous = await session.get(VisaCase, body.previous_case_id)
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        if previous.contact_id not in existing:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation")
    if existing:
        found = set(await session.scalars(select(Contact.id).where(Contact.id.in_(existing))))
        if set(existing) - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        if not allow_case_in_work and (asked := await _cases_in_work(session, existing)):
            return asked

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
            previous_case_id=(
                previous.id if previous and previous.contact_id == applicant_id else None
            ),
            stage=stage,
            created_by=user.id,
            **fields,
        )
        for number, applicant_id in zip(numbers, applicant_ids, strict=True)
    ]
    session.add_all(cases)
    await session.flush()
    for case in cases:
        linked = {"previous": str(case.previous_case_id)} if case.previous_case_id else None
        _audit(session, AuditAction.CASE_CREATED, user, case, linked)
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


class BoardVia(BaseModel):
    contact_id: uuid.UUID
    full_name: str


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
    # Whose consultation it came from, when that is someone else (pilot fix 2).
    via: BoardVia | None = None


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
    result_period: Annotated[ResultPeriod, Query()] = ResultPeriod.MONTH,
    result_from: Annotated[Day | None, Query()] = None,
    result_to: Annotated[Day | None, Query()] = None,
) -> Board:
    """Kanban of the client index: cases in work by stage, results decided in
    `result_period` (a month by default; with `dates`, from `result_from` to `result_to`,
    both days included, either end open), and clients after a consultation without a case
    in work."""
    if result_from is not None and result_to is not None and result_from > result_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "dates_order")
    today = await workspace_today(session, user.tenant_id)
    first, last = result_window(result_period, today, result_from, result_to)
    decided = func.coalesce(VisaCase.decided_on, cast(VisaCase.updated_at, Date))
    shown = []
    if first is not None:
        shown.append(decided >= first)
    if last is not None:
        shown.append(decided <= last)
    is_result = VisaCase.stage == CaseStage.RESULT
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(or_(~is_result, and_(true(), *shown)))
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    if channel_id is not None:
        query = query.where(Contact.lead_channel_id == channel_id)
    if registrar_id is not None:
        query = query.where(VisaCase.registrar_id == registrar_id)
    # Cases in work first: however many results the period holds, they never push a case
    # in work off the board.
    rows = (
        await session.execute(query.order_by(is_result, VisaCase.number.desc()).limit(BOARD_LIMIT))
    ).all()
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
        for c, name in rows
    ]
    # One card per case: the applicant's, «через …» when it came from someone else's
    # consultation; that contact goes with the case, not to «Консультация» (pilot fix 2).
    sources = await sources_of(session, [c for c, _ in rows])
    for out in cases:
        source = sources.get(out.id)
        if source is not None and source.contact_id != out.contact_id:
            out.via = BoardVia(contact_id=source.contact_id, full_name=source.full_name)

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
            lead_query = lead_query.where(any_(latest.c.destination_ids) == country_id)
        if channel_id is not None:
            lead_query = lead_query.where(Contact.lead_channel_id == channel_id)
        for contact_id, name, lead_tab, held_on, decision, destinations in await session.execute(
            lead_query.order_by(since.desc()).limit(BOARD_LIMIT)
        ):
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
    # «Результат» needs each case's own result (CRM audit 2026-11-18): the board opens
    # the case form for it instead.
    if body.stage == CaseStage.RESULT and any(case.outcome is None for case in cases):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "outcome_required")
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
    await _open_card_of(session, user, case)
    changes = body.model_dump(
        exclude_unset=True, exclude={"fill_group", "apply_to_group", "result_to_group"}
    )
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
        # A result taken away: «Результат» needs one, the dates say where the case is.
        if changes["stage"] == CaseStage.RESULT and "outcome" in new and new["outcome"] is None:
            submission_on = changes.get("submission_on", case.submission_on)
            today = await workspace_today(session, user.tenant_id)
            changes["stage"] = by_dates(submission_on, today)
    # A move into «Результат», or a result cleared there, needs the result; a case closed
    # without one before this rule keeps its stage while edited.
    if (
        changes["stage"] == CaseStage.RESULT
        and changes.get("outcome", case.outcome) is None
        and (case.stage != CaseStage.RESULT or case.outcome is not None)
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "outcome_required")
    changed = sorted(f for f, v in changes.items() if getattr(case, f) != v)
    stage_before = case.stage
    for field, value in changes.items():
        setattr(case, field, value)
    # Before anything is looked up: a query sends the case to the database, where a
    # wrong result fails the table's checks instead of being answered here.
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
    await _check_place(
        session, case.destination_id, case.submission_country_id, case.visa_center_id
    )
    if changed:
        _audit(session, AuditAction.CASE_UPDATED, user, case, {"fields": ",".join(changed)})
    if body.fill_group and case.group_id is not None:
        await _fill_group(session, user, case)
    shared = {f: changes[f] for f in changed} if body.apply_to_group else {}
    # Only a result entered or changed here goes on: the form sent again copies nothing.
    result = (
        {f: getattr(case, f) for f in RESULT_FIELDS}
        if body.result_to_group
        and case.outcome is not None
        and any(f in RESULT_FIELDS for f in changed)
        else None
    )
    if case.group_id is not None and (shared or result):
        await _apply_to_group(session, user, case, shared, stage_before, result)
    await session.commit()
    return await _one_out(session, case)


async def _apply_to_group(
    session: AsyncSession,
    user: CurrentUser,
    case: VisaCase,
    changed: dict[str, Any],
    stage_before: CaseStage,
    result: dict[str, Any] | None,
) -> None:
    """The family is filed together: what the case changed goes to the rest of its group,
    apart from the result (owner's decision 2026-11-15), and the result goes into the
    cases without one when asked (pilot fix 7). The place and the result are the case's,
    already checked, so the members need no check of their own. Only the cases the user
    sees are read, so an employee reaches only those (`employee_scope`)."""
    place = {f: getattr(case, f) for f in PLACE_FIELDS}
    members = await session.scalars(
        select(VisaCase).where(VisaCase.group_id == case.group_id, VisaCase.id != case.id)
    )
    for member in members:
        taken = for_group_member(changed, place, stage_before, member.stage, member.outcome, result)
        fields = sorted(f for f, v in taken.items() if getattr(member, f) != v)
        for field in fields:
            setattr(member, field, taken[field])
        if fields:
            _audit(session, AuditAction.CASE_UPDATED, user, member, {"fields": ",".join(fields)})


async def _fill_group(session: AsyncSession, user: CurrentUser, case: VisaCase) -> None:
    """The visa-centre number and the application email (with whose it is) go to the
    other members of the group that have none yet."""
    members = await session.scalars(
        select(VisaCase).where(VisaCase.group_id == case.group_id, VisaCase.id != case.id)
    )
    for member in members:
        changed = []
        if case.vac_number is not None and member.vac_number is None:
            member.vac_number = case.vac_number
            changed.append("vac_number")
        if case.application_email is not None and member.application_email is None:
            member.application_email = case.application_email
            member.application_email_owner = case.application_email_owner
            changed += ["application_email", "application_email_owner"]
        if changed:
            _audit(session, AuditAction.CASE_UPDATED, user, member, {"fields": ",".join(changed)})


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
            outcome=c.outcome,
        )
        for c, name in rows
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
    await _open_card_of(session, user, case)
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


async def _leave(session: AsyncSession, user: CurrentUser, case: VisaCase) -> None:
    """The case leaves its group; a group left with one case is dissolved."""
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


@router.delete("/cases/{case_id}/group", status_code=status.HTTP_204_NO_CONTENT)
async def leave_group(case_id: uuid.UUID, user: Member, session: TenantSession) -> Response:
    """The case leaves its group; a group left with one case is dissolved."""
    case = await _case(session, case_id)
    if case.group_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    await _leave(session, user, case)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Deleting a case -----------------------------------------------------------------


class CaseDeletion(BaseModel):
    """What goes with the case, for the confirmation; a case with money stays."""

    has_money: bool
    payments: int
    expenses: int
    registrar_fee: bool
    attempts: int
    queue_entries: int
    package_items: int
    bookings: int
    outcome: bool
    employees: int
    # The other cases of its submission group: they stay, the case leaves the group.
    group_members: int


async def _deletable(session: AsyncSession, user: CurrentUser, case_id: uuid.UUID) -> VisaCase:
    """Another workspace's case is not found; an employee may not delete one."""
    case = await _case(session, case_id)
    if user.role != UserRole.SPECIALIST:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return case


async def _what_goes(session: AsyncSession, case: VisaCase) -> CaseDeletion:
    async def count(model: Any) -> int:
        return await session.scalar(select(func.count()).where(model.case_id == case.id)) or 0

    payments, expenses = await count(CasePayment), await count(CaseExpense)
    registrar_fee = (
        await session.scalar(
            select(CaseFinance.registrar_fee_amount).where(CaseFinance.case_id == case.id)
        )
        is not None
    )
    group_members = 0
    if case.group_id is not None:
        group_members = (
            await session.scalar(
                select(func.count()).where(
                    VisaCase.group_id == case.group_id, VisaCase.id != case.id
                )
            )
            or 0
        )
    return CaseDeletion(
        has_money=bool(payments or expenses or registrar_fee),
        payments=payments,
        expenses=expenses,
        registrar_fee=registrar_fee,
        attempts=await count(ApplicationAttempt),
        queue_entries=await count(WaitingListEntry),
        package_items=await count(PackageItem),
        bookings=await count(Booking),
        outcome=case.outcome is not None,
        employees=await count(EmployeeCase),
        group_members=group_members,
    )


@router.get("/cases/{case_id}/deletion")
async def case_deletion(case_id: uuid.UUID, user: Member, session: TenantSession) -> CaseDeletion:
    """What the confirmation lists before a case is deleted."""
    case = await _deletable(session, user, case_id)
    return await _what_goes(session, case)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: uuid.UUID, user: Member, session: TenantSession) -> Response:
    """A case opened by mistake — a duplicate (pilot fix 1, docs/decisions.md). The
    specialist only. A case with money (payments, expenses, a talon) stays until they are
    deleted: the money must not vanish from the books unnoticed. The rest of the case —
    attempts, the queue, package marks, bookings, the result, employees' grants — goes with
    it by the foreign keys (ON DELETE CASCADE); vault accounts and templates given to the
    client stay with the client. None of them holds a stored file. The number is not given
    again (migration 0038), and a group the case was in is rebuilt without it."""
    case = await _deletable(session, user, case_id)
    if (await _what_goes(session, case)).has_money:
        raise HTTPException(status.HTTP_409_CONFLICT, "case_has_money")
    group_id = case.group_id
    if group_id is not None:
        await _leave(session, user, case)
    _audit(
        session,
        AuditAction.CASE_DELETED,
        user,
        case,
        {"number": case.number} | ({"group": str(group_id)} if group_id else {}),
    )
    await session.execute(
        update(Tenant)
        .where(Tenant.id == case.tenant_id)
        .values(max_deleted_case_number=func.greatest(Tenant.max_deleted_case_number, case.number))
    )
    await session.execute(delete(VisaCase).where(VisaCase.id == case.id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

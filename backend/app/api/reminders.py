"""«Требует внимания» (backlog 1.11): reminders computed from the workspace's data — no
messages on stage 1, the list is shown on the home screen and in the client card. Rules
and windows: app/core/reminders.py; the specialist sets the windows.

Closed by the «tasks» module; each kind also needs the module its data belongs to
(birthdays and thinking clients — «clients», case dates — «cases», money — «finances»).
«Готово» hides one occurrence for the whole workspace.
"""

import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Date, Select, and_, cast, exists, extract, func, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.client_status import Tab, latest_consultation, tab_of
from app.api.deps import (
    CurrentUser,
    TenantSession,
    effective_modules,
    require_module,
    require_specialist,
    workspace_today,
)
from app.api.registrar_ledger import ledger_summaries
from app.core import reminders as rules
from app.core.audit import record
from app.core.bookings import LIVE
from app.core.bookings import deadline as booking_deadline_of
from app.core.cases import appeal_until
from app.core.consultation_checks import add_months
from app.core.employee_limits import LimitKind, day_start
from app.core.finance import balance
from app.core.reminders import ReminderKind
from app.models import (
    ApplicationAttempt,
    AuditAction,
    AuditEvent,
    Booking,
    BookingKind,
    CaseFinance,
    CasePayment,
    CaseStage,
    Consultation,
    Contact,
    CountryRule,
    Decision,
    ReminderDismissal,
    Tenant,
    User,
    UserRole,
    VisaCase,
    VisaOutcome,
    WaitingListEntry,
)

router = APIRouter(tags=["reminders"], dependencies=[Depends(require_module("tasks"))])

Member = Annotated[CurrentUser, Depends(require_module("tasks"))]
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

# How long «an employee went over a limit» stays in the list without «Готово».
LIMIT_SIGNAL_DAYS = 7


class ReminderOut(BaseModel):
    # Names the occurrence for «Готово»; None — cannot be dismissed (it goes when done).
    key: str | None
    kind: ReminderKind
    # The day it came up, and the day of the event (submission, deadline, birthday…).
    occurs_on: date | None
    due_on: date | None
    days_left: int | None
    # Thinking clients and a follow-up: the day of the consultation; an unsigned contract:
    # the day the case was opened; a balance once in the queue: the day queued.
    since: date | None = None
    contact_id: uuid.UUID | None = None
    contact_name: str | None = None
    case_id: uuid.UUID | None = None
    case_number: int | None = None
    destination_id: uuid.UUID | None = None
    booking_kind: BookingKind | None = None
    registrar_id: uuid.UUID | None = None
    registrar_name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    # An employee over a daily limit (card_limit, password_limit).
    employee_id: uuid.UUID | None = None
    employee_name: str | None = None
    limit: int | None = None


class DismissIn(BaseModel):
    key: str = Field(max_length=120)


Days = Annotated[int, Field(ge=1, le=60)]


class SettingsIO(BaseModel):
    """Reminder windows of the workspace (defaults — docs/clients-module.md)."""

    submission_days: Days
    balance_days: Days
    application_days: Days
    booking_days: Days
    thinking_months: list[Annotated[int, Field(ge=1, le=24)]] = Field(min_length=1, max_length=3)
    visa_expiry_months: Annotated[int, Field(ge=1, le=12)]
    appeal_days: Days
    consultation_days: Days
    contract_days: Days
    questionnaire_days: Days

    @field_validator("thinking_months")
    @classmethod
    def _sorted(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


def settings_of(tenant: Tenant) -> rules.Settings:
    stored = dict(tenant.reminder_settings or {})
    if "thinking_months" in stored:
        stored["thinking_months"] = tuple(stored["thinking_months"])
    known = rules.Settings.__dataclass_fields__
    return rules.Settings(**{k: v for k, v in stored.items() if k in known})


def _settings_out(s: rules.Settings) -> SettingsIO:
    values = asdict(s)
    values["thinking_months"] = list(s.thinking_months)
    return SettingsIO(**values)


# --- Collecting ----------------------------------------------------------------------


class _Collector:
    def __init__(self, today: date) -> None:
        self.today = today
        self.items: list[ReminderOut] = []

    def add(
        self,
        kind: ReminderKind,
        subject: uuid.UUID | None,
        occurs_on: date | None,
        due_on: date | None,
        **fields: Any,
    ) -> None:
        self.items.append(
            ReminderOut(
                key=rules.key(kind, subject, occurs_on) if subject and occurs_on else None,
                kind=kind,
                occurs_on=occurs_on,
                due_on=due_on,
                days_left=(due_on - self.today).days if due_on else None,
                **fields,
            )
        )


def _case_fields(case: VisaCase, name: str) -> dict[str, Any]:
    return {
        "contact_id": case.contact_id,
        "contact_name": name,
        "case_id": case.id,
        "case_number": case.number,
        "destination_id": case.destination_id,
    }


def _for_contact(query: Select, column: Any, contact_id: uuid.UUID | None) -> Select:
    """In a client's card: only that client's reminders."""
    return query if contact_id is None else query.where(column == contact_id)


async def _birthdays(session: AsyncSession, out: _Collector, contact_id: uuid.UUID | None) -> None:
    query = select(Contact.id, Contact.full_name, Contact.birth_date).where(
        Contact.birth_date.is_not(None),
        extract("month", Contact.birth_date) == out.today.month,
    )
    for cid, name, born in await session.execute(_for_contact(query, Contact.id, contact_id)):
        day = rules.birthday(born, out.today)
        if day is not None:
            out.add(ReminderKind.BIRTHDAY, cid, day, day, contact_id=cid, contact_name=name)


async def _thinking(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    latest = latest_consultation()
    query = (
        select(Contact.id, Contact.full_name, latest.c.held_on)
        .outerjoin(latest, true())
        .where(tab_of(latest) == Tab.THINKING.value)
    )
    for cid, name, held_on in await session.execute(_for_contact(query, Contact.id, contact_id)):
        day = rules.thinking(held_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.THINKING,
                cid,
                day,
                day,
                since=held_on,
                contact_id=cid,
                contact_name=name,
            )


async def _consultations(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    """A consultation ahead, not held yet (no decision), and the day agreed at the latest
    consultation to hear the decision, while there is none or the client still thinks."""
    query = (
        select(Consultation, Contact.full_name)
        .join(Contact, Contact.id == Consultation.contact_id)
        .where(
            Consultation.decision.is_(None),
            Consultation.held_on.between(
                out.today, out.today + timedelta(days=s.consultation_days)
            ),
        )
    )
    for c, name in await session.execute(_for_contact(query, Consultation.contact_id, contact_id)):
        day = rules.consultation(c.held_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.CONSULTATION,
                c.id,
                day,
                c.held_on,
                contact_id=c.contact_id,
                contact_name=name,
            )

    later = aliased(Consultation)
    query = (
        select(Consultation, Contact.full_name)
        .join(Contact, Contact.id == Consultation.contact_id)
        .where(
            Consultation.follow_up_on.between(add_months(out.today, -1), out.today),
            or_(Consultation.decision.is_(None), Consultation.decision == Decision.THINKING),
            ~exists().where(
                later.contact_id == Consultation.contact_id,
                later.held_on > Consultation.held_on,
            ),
        )
    )
    for c, name in await session.execute(_for_contact(query, Consultation.contact_id, contact_id)):
        assert c.follow_up_on is not None
        day = rules.follow_up(c.follow_up_on, out.today)
        if day is not None:
            out.add(
                ReminderKind.FOLLOW_UP,
                c.id,
                day,
                c.follow_up_on,
                since=c.held_on,
                contact_id=c.contact_id,
                contact_name=name,
            )


async def _balances(
    session: AsyncSession, case_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal | None]:
    """What the client still owes on each case (app/core/finance.py)."""
    owed: dict[uuid.UUID, Decimal | None] = {}
    if not case_ids:
        return owed
    paid: dict[uuid.UUID, list[Decimal]] = defaultdict(list)
    for case_id, amount in await session.execute(
        select(CasePayment.case_id, CasePayment.amount_base).where(
            CasePayment.case_id.in_(case_ids)
        )
    ):
        paid[case_id].append(amount)
    for f in await session.scalars(select(CaseFinance).where(CaseFinance.case_id.in_(case_ids))):
        owed[f.case_id] = balance(f.price, f.consultation_credit, paid[f.case_id])
    return owed


async def _submissions(
    session: AsyncSession,
    out: _Collector,
    s: rules.Settings,
    contact_id: uuid.UUID | None,
    money: bool,
    currency: str,
) -> None:
    horizon = max(s.submission_days, s.balance_days if money else 0)
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            VisaCase.outcome.is_(None),
            VisaCase.submission_on.between(
                out.today, date.fromordinal(out.today.toordinal() + horizon)
            ),
        )
    )
    rows = (await session.execute(_for_contact(query, VisaCase.contact_id, contact_id))).all()
    owed = await _balances(session, [case.id for case, _ in rows]) if money else {}
    for case, name in rows:
        assert case.submission_on is not None
        day = rules.submission(case.submission_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.SUBMISSION,
                case.id,
                day,
                case.submission_on,
                **_case_fields(case, name),
            )
        if money:
            left = owed.get(case.id)
            day = rules.balance_due(case.submission_on, left, out.today, s)
            if day is not None:
                out.add(
                    ReminderKind.BALANCE_DUE,
                    case.id,
                    day,
                    case.submission_on,
                    amount=left,
                    currency=currency,
                    **_case_fields(case, name),
                )


async def _application_deadlines(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    query = (
        select(ApplicationAttempt, VisaCase, Contact.full_name)
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            ApplicationAttempt.outcome.is_(None),
            ApplicationAttempt.deadline_on
            <= date.fromordinal(out.today.toordinal() + s.application_days),
        )
    )
    for attempt, case, name in await session.execute(
        _for_contact(query, VisaCase.contact_id, contact_id)
    ):
        day = rules.application_deadline(attempt.deadline_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.APPLICATION_DEADLINE,
                attempt.id,
                day,
                attempt.deadline_on,
                **_case_fields(case, name),
            )


async def _booking_deadlines(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    horizon = date.fromordinal(out.today.toordinal() + s.booking_days)
    query = (
        select(Booking, VisaCase, Contact.full_name)
        .join(VisaCase, VisaCase.id == Booking.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            Booking.status.in_(LIVE),
            VisaCase.stage != CaseStage.RESULT,
            func.coalesce(Booking.free_cancel_until, Booking.valid_until).between(
                out.today, horizon
            ),
        )
    )
    for booking, case, name in await session.execute(
        _for_contact(query, VisaCase.contact_id, contact_id)
    ):
        due = booking_deadline_of(booking.kind, booking.free_cancel_until, booking.valid_until)
        day = rules.booking_deadline(due, out.today, s) if due else None
        if day is not None:
            out.add(
                ReminderKind.BOOKING_DEADLINE,
                booking.id,
                day,
                due,
                booking_kind=booking.kind,
                **_case_fields(case, name),
            )


async def _visas(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    newer = aliased(VisaCase)
    # A client already back in work for the same country needs no reminder.
    in_work = exists().where(
        newer.contact_id == VisaCase.contact_id,
        newer.destination_id == VisaCase.destination_id,
        newer.stage != CaseStage.RESULT,
    )
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            VisaCase.outcome == VisaOutcome.VISA,
            VisaCase.visa_valid_to.between(out.today, add_months(out.today, s.visa_expiry_months)),
            ~in_work,
        )
    )
    for case, name in await session.execute(_for_contact(query, VisaCase.contact_id, contact_id)):
        assert case.visa_valid_to is not None
        day = rules.visa_expires(case.visa_valid_to, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.VISA_EXPIRES,
                case.id,
                day,
                case.visa_valid_to,
                **_case_fields(case, name),
            )


async def _appeals(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    query = (
        select(VisaCase, Contact.full_name, CountryRule.appeal_deadline_days)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .join(
            CountryRule,
            and_(
                CountryRule.destination_id == VisaCase.destination_id,
                CountryRule.submission_id == VisaCase.submission_country_id,
            ),
        )
        .where(
            VisaCase.outcome == VisaOutcome.REFUSAL,
            VisaCase.after_appeal.is_(False),
            VisaCase.decided_on.is_not(None),
            CountryRule.appeals_accepted.is_(True),
        )
    )
    for case, name, deadline_days in await session.execute(
        _for_contact(query, VisaCase.contact_id, contact_id)
    ):
        until = appeal_until(case.outcome, case.decided_on, case.after_appeal, deadline_days)
        if until is None or case.decided_on is None:
            continue
        day = rules.appeal(case.decided_on, until, out.today, s)
        if day is not None:
            out.add(ReminderKind.APPEAL, case.id, day, until, **_case_fields(case, name))


async def _contracts(
    session: AsyncSession,
    out: _Collector,
    s: rules.Settings,
    contact_id: uuid.UUID | None,
    timezone: str,
) -> None:
    """A client with a case in work and no contract date: counted from the latest such
    case, opened on its day in the specialist's time zone."""
    opened = func.max(cast(func.timezone(timezone, VisaCase.created_at), Date))
    query = (
        select(Contact.id, Contact.full_name, opened)
        .join(VisaCase, VisaCase.contact_id == Contact.id)
        .where(Contact.contract_signed_on.is_(None), VisaCase.stage != CaseStage.RESULT)
        .group_by(Contact.id, Contact.full_name)
    )
    for cid, name, opened_on in await session.execute(_for_contact(query, Contact.id, contact_id)):
        day = rules.contract_unsigned(opened_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.CONTRACT,
                cid,
                day,
                None,
                since=opened_on,
                contact_id=cid,
                contact_name=name,
            )


async def _questionnaires(
    session: AsyncSession, out: _Collector, s: rules.Settings, contact_id: uuid.UUID | None
) -> None:
    query = (
        select(VisaCase, Contact.full_name)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(
            VisaCase.questionnaire_received_on.is_(None),
            VisaCase.questionnaire_due_on <= out.today + timedelta(days=s.questionnaire_days),
            VisaCase.stage != CaseStage.RESULT,
        )
    )
    for case, name in await session.execute(_for_contact(query, VisaCase.contact_id, contact_id)):
        assert case.questionnaire_due_on is not None
        day = rules.questionnaire(case.questionnaire_due_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.QUESTIONNAIRE,
                case.id,
                day,
                case.questionnaire_due_on,
                **_case_fields(case, name),
            )


async def _queued_balances(
    session: AsyncSession,
    out: _Collector,
    s: rules.Settings,
    contact_id: uuid.UUID | None,
    currency: str,
) -> None:
    """The client pays the rest once put in the waiting list: from the first day queued,
    while the case is still in the queue (taken off it — no longer)."""
    queued_on = func.min(WaitingListEntry.queued_on)
    query = (
        select(VisaCase, Contact.full_name, queued_on)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .join(WaitingListEntry, WaitingListEntry.case_id == VisaCase.id)
        .where(VisaCase.outcome.is_(None))
        .group_by(VisaCase.id, Contact.full_name)
        .having(func.bool_or(WaitingListEntry.removed_on.is_(None)))
    )
    rows = (await session.execute(_for_contact(query, VisaCase.contact_id, contact_id))).all()
    owed = await _balances(session, [case.id for case, _, _ in rows])
    for case, name, first in rows:
        left = owed.get(case.id)
        day = rules.balance_queued(first, left, case.submission_on, out.today, s)
        if day is not None:
            out.add(
                ReminderKind.BALANCE_QUEUED,
                case.id,
                day,
                None,
                since=first,
                amount=left,
                currency=currency,
                **_case_fields(case, name),
            )


async def _registrars(session: AsyncSession, out: _Collector) -> None:
    for r in await ledger_summaries(session):
        if r.low and r.is_active:
            out.add(
                ReminderKind.REGISTRAR_ADVANCE,
                None,
                None,
                None,
                registrar_id=r.id,
                registrar_name=r.name,
                amount=r.balance,
                currency=r.currency,
            )


async def _limits(session: AsyncSession, out: _Collector, timezone: str) -> None:
    """Employees who went over a daily limit lately, for the specialist (Т.14): one item
    an employee, a limit and a day, until «Готово» or a week has passed."""
    since = day_start(timezone) - timedelta(days=LIMIT_SIGNAL_DAYS - 1)
    rows = await session.execute(
        select(AuditEvent, User)
        .join(User, User.id == AuditEvent.actor_id)
        .where(
            AuditEvent.action == AuditAction.EMPLOYEE_LIMIT_REACHED,
            AuditEvent.occurred_at >= since,
        )
        .order_by(AuditEvent.id)
    )
    zone = ZoneInfo(timezone)
    for event, employee in rows.tuples():
        details = event.details or {}
        kind = (
            ReminderKind.CARD_LIMIT
            if details.get("kind") == LimitKind.CARDS
            else ReminderKind.PASSWORD_LIMIT
        )
        out.add(
            kind,
            employee.id,
            event.occurred_at.astimezone(zone).date(),
            None,
            employee_id=employee.id,
            employee_name=employee.full_name or employee.email,
            limit=details.get("limit"),
        )


async def collect(
    session: AsyncSession, user: CurrentUser, contact_id: uuid.UUID | None = None
) -> list[ReminderOut]:
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    s = settings_of(tenant)
    modules = set(await effective_modules(session, user))
    out = _Collector(await workspace_today(session, tenant.id))
    if "clients" in modules:
        await _birthdays(session, out, contact_id)
        await _thinking(session, out, s, contact_id)
        await _consultations(session, out, s, contact_id)
    if "cases" in modules:
        money = "finances" in modules
        await _submissions(session, out, s, contact_id, money, tenant.base_currency)
        await _application_deadlines(session, out, s, contact_id)
        await _booking_deadlines(session, out, s, contact_id)
        await _visas(session, out, s, contact_id)
        await _appeals(session, out, s, contact_id)
        await _contracts(session, out, s, contact_id, tenant.timezone)
        await _questionnaires(session, out, s, contact_id)
        if money:
            await _queued_balances(session, out, s, contact_id, tenant.base_currency)
        # A registrar's balance covers every case: the specialist's, not an employee's.
        if money and contact_id is None and user.role != UserRole.EMPLOYEE:
            await _registrars(session, out)
    if contact_id is None and user.role == UserRole.SPECIALIST:
        await _limits(session, out, tenant.timezone)

    keys = [item.key for item in out.items if item.key]
    dismissed = (
        set(
            await session.scalars(
                select(ReminderDismissal.key).where(ReminderDismissal.key.in_(keys))
            )
        )
        if keys
        else set()
    )
    order = list(ReminderKind)
    shown = [item for item in out.items if item.key not in dismissed]
    shown.sort(
        key=lambda i: (
            # An employee over a limit (Т.14) is the first thing the specialist sees.
            i.kind not in (ReminderKind.CARD_LIMIT, ReminderKind.PASSWORD_LIMIT),
            i.days_left is None,
            i.days_left if i.days_left is not None else 0,
            order.index(i.kind),
            i.contact_name or i.registrar_name or "",
        )
    )
    return shown


# --- Endpoints -----------------------------------------------------------------------


@router.get("/reminders")
async def list_reminders(
    user: Member,
    session: TenantSession,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[ReminderOut]:
    return await collect(session, user, contact_id)


@router.post("/reminders/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(body: DismissIn, user: Member, session: TenantSession) -> None:
    """«Готово»: hide this occurrence. Only a reminder the user sees now can be dismissed."""
    if rules.parse_key(body.key) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_reminder")
    if body.key not in {item.key for item in await collect(session, user)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    await session.execute(
        insert(ReminderDismissal)
        .values(tenant_id=user.tenant_id, key=body.key, dismissed_by=user.id)
        .on_conflict_do_nothing(constraint="uq_reminder_dismissals_key")
    )
    await session.commit()


@router.get("/reminders/settings")
async def get_settings(user: Member, session: TenantSession) -> SettingsIO:
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    return _settings_out(settings_of(tenant))


@router.put("/reminders/settings")
async def put_settings(body: SettingsIO, user: Specialist, session: TenantSession) -> SettingsIO:
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    tenant.reminder_settings = body.model_dump()
    record(session, AuditAction.REMINDER_SETTINGS_UPDATED, actor=user)
    await session.commit()
    return _settings_out(settings_of(tenant))

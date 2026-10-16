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
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, and_, exists, extract, func, select, true
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
from app.core.finance import balance
from app.core.reminders import ReminderKind
from app.models import (
    ApplicationAttempt,
    AuditAction,
    Booking,
    BookingKind,
    CaseFinance,
    CasePayment,
    CaseStage,
    Contact,
    CountryRule,
    ReminderDismissal,
    Tenant,
    VisaCase,
    VisaOutcome,
)

router = APIRouter(tags=["reminders"], dependencies=[Depends(require_module("tasks"))])

Member = Annotated[CurrentUser, Depends(require_module("tasks"))]
Specialist = Annotated[CurrentUser, Depends(require_specialist)]


class ReminderOut(BaseModel):
    # Names the occurrence for «Готово»; None — cannot be dismissed (it goes when done).
    key: str | None
    kind: ReminderKind
    # The day it came up, and the day of the event (submission, deadline, birthday…).
    occurs_on: date | None
    due_on: date | None
    days_left: int | None
    # Thinking clients: the day of the consultation.
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
    owed: dict[uuid.UUID, Decimal | None] = {}
    if money and rows:
        ids = [case.id for case, _ in rows]
        paid: dict[uuid.UUID, list[Decimal]] = defaultdict(list)
        for case_id, amount in await session.execute(
            select(CasePayment.case_id, CasePayment.amount_base).where(CasePayment.case_id.in_(ids))
        ):
            paid[case_id].append(amount)
        for f in await session.scalars(select(CaseFinance).where(CaseFinance.case_id.in_(ids))):
            owed[f.case_id] = balance(f.price, f.consultation_credit, paid[f.case_id])
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
    if "cases" in modules:
        money = "finances" in modules
        await _submissions(session, out, s, contact_id, money, tenant.base_currency)
        await _application_deadlines(session, out, s, contact_id)
        await _booking_deadlines(session, out, s, contact_id)
        await _visas(session, out, s, contact_id)
        await _appeals(session, out, s, contact_id)
        if money and contact_id is None:
            await _registrars(session, out)

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

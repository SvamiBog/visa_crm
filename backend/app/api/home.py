"""Главный экран (backlog 1.12): what to do today at a glance — metrics, the nearest
submissions, the month's funnel, the queues and the money. «Требует внимания» comes from
app/api/reminders.py. Spec: docs/clients-module.md → «Картотека и главный экран».

Open to every workspace member; each block needs the modules its data belongs to
(clients — people and consultations; cases — submissions, queues and the funnel;
finances — money), so an employee sees only what they were given.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, distinct, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    TenantSession,
    effective_modules,
    require_module,
    require_tenant_user,
)
from app.api.registrar_ledger import ledger_summaries
from app.core.consultation_checks import add_months
from app.core.finance import ZERO, balance, money, partner_payout
from app.core.queues import SUBMITTED_STAGES
from app.models import (
    ApplicationAttempt,
    CaseFinance,
    CasePayment,
    CaseStage,
    Consultation,
    Contact,
    Decision,
    Tenant,
    VisaCase,
    VisaOutcome,
    WaitingListEntry,
)

router = APIRouter(tags=["home"])

Member = Annotated[CurrentUser, Depends(require_tenant_user)]

UPCOMING = 8  # lines in «Ближайшие подачи»
WEEK = 7


class ClientsBlock(BaseModel):
    contacts: int
    # People with a case not at the result stage (the «В работе» tab).
    in_work: int
    # This month's consultations, and those that went to work: a «yes» or a case opened.
    consultations: int
    consultations_to_work: int
    cases_opened: int


class Upcoming(BaseModel):
    """A submission day of one client, or of a family submitting together."""

    submission_on: date
    case_id: uuid.UUID
    case_number: int
    contact_id: uuid.UUID
    contact_name: str
    others: int  # other members of the group submitting that day
    destination_id: uuid.UUID
    visa_center_id: uuid.UUID | None
    stage: CaseStage
    # What these clients still owe; None without the money module or a price.
    balance: Decimal | None


class SubmissionsBlock(BaseModel):
    week: int  # cases submitting today and in the next six days
    next_on: date | None
    upcoming: list[Upcoming]


class FunnelBlock(BaseModel):
    """Consultations held in the month and how far they went: decided to work, queued or
    got a date, submitted, got a visa."""

    month: date
    consultations: int
    decided: int
    queued: int
    submitted: int
    visas: int


class QueueCountry(BaseModel):
    country_id: uuid.UUID
    waiting: int
    open_attempts: int
    # Days from the queue to the letter over this workspace's clients who got one.
    average_days: int | None
    next_deadline: date | None


class QueuesBlock(BaseModel):
    waiting: int
    open_attempts: int
    average_days: int | None
    countries: list[QueueCountry]


class RegistrarDue(BaseModel):
    currency: str
    amount: Decimal
    registrars: int


class MoneyBlock(BaseModel):
    currency: str
    debt: Decimal  # what clients still owe, over cases with a positive balance
    debtors: int
    partners_due: Decimal  # payouts counted but not marked «выплачено»
    partner_cases: int
    registrars_due: list[RegistrarDue]  # debts to registrars, in their currencies


class HomeOut(BaseModel):
    today: date
    clients: ClientsBlock | None
    submissions: SubmissionsBlock | None
    funnel: FunnelBlock | None
    queues: QueuesBlock | None
    money: MoneyBlock | None


# --- Blocks --------------------------------------------------------------------------


async def _clients(session: AsyncSession, today: date, month_from: datetime) -> ClientsBlock:
    start, end = today.replace(day=1), add_months(today.replace(day=1), 1)
    has_case = exists().where(VisaCase.consultation_id == Consultation.id)
    consultations, to_work = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(or_(Consultation.decision == Decision.YES, has_case)),
            ).where(Consultation.held_on >= start, Consultation.held_on < end)
        )
    ).one()
    return ClientsBlock(
        contacts=await session.scalar(select(func.count()).select_from(Contact)) or 0,
        in_work=await session.scalar(
            select(func.count(distinct(VisaCase.contact_id))).where(
                VisaCase.stage != CaseStage.RESULT
            )
        )
        or 0,
        consultations=consultations,
        consultations_to_work=to_work,
        cases_opened=await session.scalar(
            select(func.count()).where(VisaCase.created_at >= month_from)
        )
        or 0,
    )


async def _balances(session: AsyncSession, case_ids: list[uuid.UUID] | None = None):
    """(finance, contact, balance) of cases with finances; all of them, or these."""
    paid = (
        select(CasePayment.case_id, func.sum(CasePayment.amount_base).label("paid"))
        .group_by(CasePayment.case_id)
        .subquery()
    )
    query = (
        select(CaseFinance, VisaCase.contact_id, func.coalesce(paid.c.paid, ZERO))
        .join(VisaCase, VisaCase.id == CaseFinance.case_id)
        .outerjoin(paid, paid.c.case_id == CaseFinance.case_id)
    )
    if case_ids is not None:
        query = query.where(CaseFinance.case_id.in_(case_ids))
    return [
        (f, contact_id, balance(f.price, f.consultation_credit, [amount]))
        for f, contact_id, amount in await session.execute(query)
    ]


async def _submissions(session: AsyncSession, today: date, money_on: bool) -> SubmissionsBlock:
    ahead = and_(VisaCase.outcome.is_(None), VisaCase.submission_on >= today)
    week = await session.scalar(
        select(func.count()).where(ahead, VisaCase.submission_on < today + timedelta(days=WEEK))
    )
    next_on = await session.scalar(select(func.min(VisaCase.submission_on)).where(ahead))
    # Enough cases for the first lines even when families take several each.
    rows = (
        await session.execute(
            select(VisaCase, Contact.full_name)
            .join(Contact, Contact.id == VisaCase.contact_id)
            .where(ahead)
            .order_by(VisaCase.submission_on, VisaCase.number)
            .limit(UPCOMING * 6)
        )
    ).all()
    lines: dict[tuple[uuid.UUID, date], list[tuple[VisaCase, str]]] = {}
    for case, name in rows:
        assert case.submission_on is not None
        lines.setdefault((case.group_id or case.id, case.submission_on), []).append((case, name))
    shown = list(lines.values())[:UPCOMING]
    owed: dict[uuid.UUID, Decimal | None] = {}
    if money_on and shown:
        ids = [case.id for line in shown for case, _ in line]
        owed = {f.case_id: left for f, _, left in await _balances(session, ids)}

    def line_balance(line: list[tuple[VisaCase, str]]) -> Decimal | None:
        known = [owed[case.id] for case, _ in line if owed.get(case.id) is not None]
        if not known:
            return None
        return money(sum((left for left in known if left and left > 0), ZERO))

    def upcoming(line: list[tuple[VisaCase, str]]) -> Upcoming:
        first, name = line[0]
        assert first.submission_on is not None
        return Upcoming(
            submission_on=first.submission_on,
            case_id=first.id,
            case_number=first.number,
            contact_id=first.contact_id,
            contact_name=name,
            others=len(line) - 1,
            destination_id=first.destination_id,
            visa_center_id=first.visa_center_id,
            stage=first.stage,
            balance=line_balance(line),
        )

    return SubmissionsBlock(
        week=week or 0, next_on=next_on, upcoming=[upcoming(line) for line in shown]
    )


async def _funnel(session: AsyncSession, month: date) -> FunnelBlock:
    """Each step counts consultations of the month one of whose cases reached it, so the
    steps only narrow: a date or a queue needs a case, a visa needs a submission."""
    from_it = VisaCase.consultation_id == Consultation.id
    queued = exists().where(
        from_it,
        or_(
            VisaCase.stage != CaseStage.WAITING,
            VisaCase.submission_on.is_not(None),
            exists().where(WaitingListEntry.case_id == VisaCase.id),
            exists().where(ApplicationAttempt.case_id == VisaCase.id),
        ),
    )
    submitted = exists().where(
        from_it,
        VisaCase.stage.in_(SUBMITTED_STAGES),
        or_(VisaCase.outcome.is_(None), VisaCase.outcome != VisaOutcome.NOT_SUBMITTED),
    )
    visas = exists().where(from_it, VisaCase.outcome == VisaOutcome.VISA)
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(
                    or_(Consultation.decision == Decision.YES, exists().where(from_it))
                ),
                func.count().filter(queued),
                func.count().filter(submitted),
                func.count().filter(visas),
            ).where(Consultation.held_on >= month, Consultation.held_on < add_months(month, 1))
        )
    ).one()
    return FunnelBlock(
        month=month,
        consultations=row[0],
        decided=row[1],
        queued=row[2],
        submitted=row[3],
        visas=row[4],
    )


async def _queues(session: AsyncSession, today: date) -> QueuesBlock:
    """Like «Очереди и записи»: who still waits for a letter, which applications are open."""
    countries: dict[uuid.UUID, QueueCountry] = {}

    def row(country: uuid.UUID) -> QueueCountry:
        return countries.setdefault(
            country,
            QueueCountry(
                country_id=country,
                waiting=0,
                open_attempts=0,
                average_days=None,
                next_deadline=None,
            ),
        )

    waiting = await session.execute(
        select(VisaCase.destination_id, func.count())
        .select_from(WaitingListEntry)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(
            WaitingListEntry.removed_on.is_(None),
            WaitingListEntry.letter_on.is_(None),
            VisaCase.stage.not_in(SUBMITTED_STAGES),
        )
        .group_by(VisaCase.destination_id)
    )
    for country, count in waiting.tuples():
        row(country).waiting = count
    attempts = await session.execute(
        select(
            VisaCase.destination_id,
            func.count(),
            func.min(ApplicationAttempt.deadline_on).filter(
                ApplicationAttempt.deadline_on >= today
            ),
        )
        .select_from(ApplicationAttempt)
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .where(ApplicationAttempt.outcome.is_(None))
        .group_by(VisaCase.destination_id)
    )
    for country, count, deadline in attempts.tuples():
        row(country).open_attempts, row(country).next_deadline = count, deadline
    waits = await session.execute(
        select(
            VisaCase.destination_id,
            func.sum(WaitingListEntry.letter_on - WaitingListEntry.queued_on),
            func.count(),
        )
        .select_from(WaitingListEntry)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(WaitingListEntry.letter_on.is_not(None))
        .group_by(VisaCase.destination_id)
    )
    total_days = total_letters = 0
    for country, days, letters in waits.tuples():
        total_days, total_letters = total_days + days, total_letters + letters
        if country in countries:
            countries[country].average_days = round(days / letters)
    shown = sorted(countries.values(), key=lambda r: -(r.waiting + r.open_attempts))
    return QueuesBlock(
        waiting=sum(r.waiting for r in shown),
        open_attempts=sum(r.open_attempts for r in shown),
        average_days=round(total_days / total_letters) if total_letters else None,
        countries=shown,
    )


async def _money(session: AsyncSession, currency: str) -> MoneyBlock:
    debt, debtors = ZERO, set()
    partners_due, partner_cases = ZERO, 0
    for f, contact_id, left in await _balances(session):
        if left is not None and left > 0:
            debt += left
            debtors.add(contact_id)
        payout = partner_payout(f.partner_terms, f.price, f.partner_percent, f.partner_fixed)
        if payout and f.partner_paid_on is None:
            partners_due += payout
            partner_cases += 1
    owed: dict[str, list[Decimal]] = defaultdict(list)
    for registrar in await ledger_summaries(session):
        if registrar.balance < 0:
            owed[registrar.currency].append(-registrar.balance)
    return MoneyBlock(
        currency=currency,
        debt=money(debt),
        debtors=len(debtors),
        partners_due=money(partners_due),
        partner_cases=partner_cases,
        registrars_due=[
            RegistrarDue(currency=c, amount=money(sum(amounts, ZERO)), registrars=len(amounts))
            for c, amounts in sorted(owed.items())
        ],
    )


# --- Endpoint ------------------------------------------------------------------------


@router.get("/home")
async def home(user: Member, session: TenantSession) -> HomeOut:
    tenant = await session.get_one(Tenant, user.tenant_id)
    zone = ZoneInfo(tenant.timezone)
    today = datetime.now(zone).date()
    month_start = today.replace(day=1)
    month_from = datetime.combine(month_start, datetime.min.time(), zone).astimezone(UTC)
    modules = set(await effective_modules(session, user))
    clients = "clients" in modules
    cases = clients and "cases" in modules
    money_on = cases and "finances" in modules
    return HomeOut(
        today=today,
        clients=await _clients(session, today, month_from) if clients else None,
        submissions=await _submissions(session, today, money_on) if cases else None,
        funnel=await _funnel(session, month_start) if cases else None,
        queues=await _queues(session, today) if cases else None,
        money=await _money(session, tenant.base_currency) if money_on else None,
    )


@router.get(
    "/home/funnel",
    dependencies=[Depends(require_module("clients")), Depends(require_module("cases"))],
)
async def month_funnel(
    session: TenantSession,
    month: Annotated[str, Query(pattern=r"^20\d\d-(0[1-9]|1[0-2])$")],
) -> FunnelBlock:
    """The funnel of another month (YYYY-MM), for leafing through it on the home screen."""
    return await _funnel(session, date.fromisoformat(f"{month}-01"))

"""Аналитика (backlog 1.13): reports of a period computed from the cards instead of pivot
tables. Spec: docs/clients-module.md → «Аналитика»; how each number is counted —
docs/decisions.md → «Аналитика»; the rules — app/core/analytics.py.

- Consultations count by the day they were held; channels and partners follow what the
  clients of those consultations came to (cases, visas, profit), whenever it happened.
- Cases count by their submission day (the appointment, else the decision day).
- Queues and applications count by the day of the letter, the date or the removal.

Closed by the «analytics» module with «clients» and «cases»; money needs «finances».
"""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, effective_modules, require_module
from app.api.home import MoneyBlock, money_block
from app.core import analytics as rules
from app.core.finance import ZERO, money, summarize
from app.core.queues import SUBMITTED_STAGES
from app.models import (
    ApplicationAttempt,
    AttemptOutcome,
    CaseExpense,
    CaseFinance,
    CasePayment,
    Consultation,
    Contact,
    CountryRule,
    Decision,
    Tenant,
    VisaCase,
    VisaCenter,
    VisaCenterNote,
    VisaOutcome,
    WaitingListEntry,
)

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("cases")),
        Depends(require_module("analytics")),
    ],
)

Member = Annotated[CurrentUser, Depends(require_module("analytics"))]

LONGEST_PERIOD = timedelta(days=3660)


class Totals(BaseModel):
    consultations: int
    to_work: int  # consultations with a «yes» or a case opened from them
    cases_opened: int
    applicants: int  # people, not cases
    families: int  # submission groups among the cases opened
    submissions: int
    not_submitted: int
    visas: int
    refusals: int
    approval: int | None
    profit: Decimal | None


class Source(BaseModel):
    consultations: int
    to_work: int
    visas: int  # cases opened from these consultations that got a visa
    profit: Decimal | None  # of those cases, whenever they were submitted


class ChannelRow(Source):
    channel_id: uuid.UUID | None


class PartnerRow(Source):
    partner_id: uuid.UUID


class MonthRow(BaseModel):
    month: date
    consultations: int
    to_work: int
    submissions: int
    visas: int
    profit: Decimal | None


class Decided(BaseModel):
    submissions: int
    not_submitted: int
    visas: int
    refusals: int
    approval: int | None
    review_days: int | None  # from the submission to the decision


class CountryRow(Decided):
    country_id: uuid.UUID
    multiple: int


class CenterRow(BaseModel):
    visa_center_id: uuid.UUID
    country_id: uuid.UUID
    submissions: int
    visas: int
    refusals: int
    approval: int | None
    wait_days: int | None  # from the queue to the letter
    attempts: float | None  # applications sent until one brought a date
    review_days: int | None
    note: str | None


class QueueRow(BaseModel):
    country_id: uuid.UUID
    visa_center_id: uuid.UUID | None
    purpose_id: uuid.UUID | None
    waiting: int  # now
    letters: int  # in the period, with the wait from the queue to the letter
    average_days: int | None
    min_days: int | None
    max_days: int | None
    removed: int


class AttemptRow(BaseModel):
    country_id: uuid.UUID
    dates: int  # applications that brought a date in the period
    average_attempts: float | None


class AnalyticsOut(BaseModel):
    start: date
    end: date
    totals: Totals
    channels: list[ChannelRow]
    partners: list[PartnerRow]
    months: list[MonthRow]
    countries: list[CountryRow]
    results: dict[rules.Result, int]
    centers: list[CenterRow]
    queues: list[QueueRow]
    attempts: list[AttemptRow]
    money: MoneyBlock | None


class NoteIn(BaseModel):
    note: str = Field(max_length=500)


# --- Counting ------------------------------------------------------------------------


@dataclass
class _Tally:
    """Counters of a group of cases of the period."""

    submissions: int = 0
    not_submitted: int = 0
    visas: int = 0
    refusals: int = 0
    multiple: int = 0
    review: list[int] = field(default_factory=list)

    def add(self, case: VisaCase, result: rules.Result | None) -> None:
        if result == rules.Result.NOT_SUBMITTED:
            self.not_submitted += 1
            return
        self.submissions += 1
        if result is None:
            return
        if rules.is_visa(result):
            self.visas += 1
            self.multiple += result.value.startswith("multiple")
        else:
            self.refusals += 1
        if case.submission_on and case.decided_on:
            self.review.append((case.decided_on - case.submission_on).days)

    def decided(self) -> dict[str, Any]:
        return {
            "submissions": self.submissions,
            "not_submitted": self.not_submitted,
            "visas": self.visas,
            "refusals": self.refusals,
            "approval": rules.approval(self.visas, self.refusals),
            "review_days": rules.average(self.review),
        }


def _result(case: VisaCase) -> rules.Result | None:
    if case.outcome is None:
        return None
    return rules.result(
        case.outcome,
        after_appeal=case.after_appeal,
        entries=case.visa_entries,
        valid_from=case.visa_valid_from,
        valid_to=case.visa_valid_to,
    )


def _case_day(case: VisaCase) -> date:
    day = case.submission_on or case.decided_on
    assert day is not None
    return day


async def _profits(
    session: AsyncSession, case_ids: list[uuid.UUID], tax_rate: Decimal
) -> dict[uuid.UUID, Decimal]:
    """Profit of each case that has a price (app/core/finance.py)."""
    if not case_ids:
        return {}
    payments: dict[uuid.UUID, list[tuple[Decimal, bool]]] = defaultdict(list)
    for case_id, amount, official in await session.execute(
        select(CasePayment.case_id, CasePayment.amount_base, CasePayment.official).where(
            CasePayment.case_id.in_(case_ids)
        )
    ):
        payments[case_id].append((amount, official))
    expenses: dict[uuid.UUID, list[Decimal]] = defaultdict(list)
    for case_id, amount in await session.execute(
        select(CaseExpense.case_id, CaseExpense.amount_base).where(
            CaseExpense.case_id.in_(case_ids)
        )
    ):
        expenses[case_id].append(amount)
    profits = {}
    for f in await session.scalars(select(CaseFinance).where(CaseFinance.case_id.in_(case_ids))):
        profit = summarize(
            price=f.price,
            consultation_credit=f.consultation_credit,
            payments=payments[f.case_id],
            registrar_fee=f.registrar_fee_base,
            partner_terms=f.partner_terms,
            partner_percent=f.partner_percent,
            partner_fixed=f.partner_fixed,
            tax_rate=tax_rate,
            expenses=expenses[f.case_id],
        ).profit
        if profit is not None:
            profits[f.case_id] = profit
    return profits


def _sum(values: list[Decimal]) -> Decimal:
    return money(sum(values, ZERO))


# --- Endpoints -----------------------------------------------------------------------


@router.get("")
async def analytics(
    user: Member,
    session: TenantSession,
    start: date,
    end: date,
    country_id: Annotated[uuid.UUID | None, Query()] = None,
) -> AnalyticsOut:
    if end < start or end - start > LONGEST_PERIOD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_period")
    tenant = await session.get_one(Tenant, user.tenant_id)
    money_on = "finances" in await effective_modules(session, user)

    def for_country(query, column: ColumnElement) -> Any:
        return query if country_id is None else query.where(column == country_id)

    # Consultations of the period and the cases opened from them.
    channel = func.coalesce(Consultation.lead_channel_id, Contact.lead_channel_id)
    query = (
        select(
            Consultation.id,
            Consultation.held_on,
            Consultation.decision,
            channel,
            Contact.partner_id,
        )
        .join(Contact, Contact.id == Consultation.contact_id)
        .where(Consultation.held_on.between(start, end))
    )
    if country_id is not None:
        query = query.where(Consultation.destination_ids.any(country_id))
    consultations = (await session.execute(query)).all()
    opened: dict[uuid.UUID, list[VisaCase]] = defaultdict(list)
    if consultations:
        for case in await session.scalars(
            select(VisaCase).where(VisaCase.consultation_id.in_([c[0] for c in consultations]))
        ):
            assert case.consultation_id is not None
            opened[case.consultation_id].append(case)

    # Cases of the period: submitted, or had a date and did not go.
    day = func.coalesce(VisaCase.submission_on, VisaCase.decided_on)
    cases = list(
        await session.scalars(
            for_country(
                select(VisaCase).where(
                    day.between(start, end),
                    or_(
                        VisaCase.stage.in_(SUBMITTED_STAGES),
                        VisaCase.outcome == VisaOutcome.NOT_SUBMITTED,
                    ),
                ),
                VisaCase.destination_id,
            )
        )
    )
    profits: dict[uuid.UUID, Decimal] = {}
    if money_on:
        ids = {c.id for c in cases} | {c.id for group in opened.values() for c in group}
        profits = await _profits(session, list(ids), tenant.tax_rate)

    def profit_of(case_list: list[VisaCase]) -> Decimal | None:
        if not money_on:
            return None
        return _sum([profits[c.id] for c in case_list if c.id in profits])

    # Sources: channels and partners.
    def source(rows: list[Any]) -> dict[str, Any]:
        from_them = [case for row in rows for case in opened[row[0]]]
        return {
            "consultations": len(rows),
            "to_work": sum(1 for row in rows if row[2] == Decision.YES or opened[row[0]]),
            "visas": sum(1 for case in from_them if case.outcome == VisaOutcome.VISA),
            "profit": profit_of(from_them),
        }

    by_channel: dict[uuid.UUID | None, list[Any]] = defaultdict(list)
    by_partner: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for row in consultations:
        by_channel[row[3]].append(row)
        if row[4] is not None:
            by_partner[row[4]].append(row)

    # Months, countries, results, centers.
    month_rows = {
        m: {"consultations": 0, "to_work": 0, "cases": []} for m in rules.months(start, end)
    }
    for row in consultations:
        bucket = month_rows[row[1].replace(day=1)]
        bucket["consultations"] += 1
        bucket["to_work"] += row[2] == Decision.YES or bool(opened[row[0]])
    countries: dict[uuid.UUID, _Tally] = defaultdict(_Tally)
    centers: dict[uuid.UUID, _Tally] = defaultdict(_Tally)
    results: Counter[rules.Result] = Counter()
    totals = _Tally()
    for case in cases:
        result = _result(case)
        month_rows[_case_day(case).replace(day=1)]["cases"].append((case, result))
        for tally in (totals, countries[case.destination_id]):
            tally.add(case, result)
        if case.visa_center_id is not None:
            centers[case.visa_center_id].add(case, result)
        if result is not None:
            results[result] += 1

    def month_row(month: date, bucket: dict[str, Any]) -> MonthRow:
        tally = _Tally()
        for case, result in bucket["cases"]:
            tally.add(case, result)
        return MonthRow(
            month=month,
            consultations=bucket["consultations"],
            to_work=bucket["to_work"],
            submissions=tally.submissions,
            visas=tally.visas,
            profit=profit_of([case for case, _ in bucket["cases"]]),
        )

    waits, attempts = await _waits(session, start, end, country_id)
    return AnalyticsOut(
        start=start,
        end=end,
        totals=Totals(
            consultations=len(consultations),
            to_work=source(consultations)["to_work"],
            **await _opened(session, tenant, start, end, country_id),
            submissions=totals.submissions,
            not_submitted=totals.not_submitted,
            visas=totals.visas,
            refusals=totals.refusals,
            approval=rules.approval(totals.visas, totals.refusals),
            profit=profit_of(cases),
        ),
        channels=sorted(
            (ChannelRow(channel_id=k, **source(rows)) for k, rows in by_channel.items()),
            key=lambda r: -r.consultations,
        ),
        partners=sorted(
            (PartnerRow(partner_id=k, **source(rows)) for k, rows in by_partner.items()),
            key=lambda r: -r.consultations,
        ),
        months=[month_row(m, bucket) for m, bucket in month_rows.items()],
        countries=sorted(
            (
                CountryRow(country_id=k, multiple=t.multiple, **t.decided())
                for k, t in countries.items()
            ),
            key=lambda r: -(r.submissions + r.not_submitted),
        ),
        results={r: n for r, n in results.items()},
        centers=await _centers(session, centers, waits, attempts, country_id),
        queues=await _queues(session, start, end, country_id),
        attempts=await _attempts(session, start, end, country_id),
        money=await money_block(session, user, tenant.base_currency) if money_on else None,
    )


async def _opened(
    session: AsyncSession, tenant: Tenant, start: date, end: date, country_id: uuid.UUID | None
) -> dict[str, int]:
    """Cases opened in the period (in the workspace's time zone): cases, people, families."""
    zone = ZoneInfo(tenant.timezone)
    since = datetime.combine(start, datetime.min.time(), zone)
    until = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone)
    query = select(
        func.count(),
        func.count(func.distinct(VisaCase.contact_id)),
        func.count(func.distinct(VisaCase.group_id)),
    ).where(VisaCase.created_at >= since, VisaCase.created_at < until)
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    cases_opened, applicants, families = (await session.execute(query)).one()
    return {"cases_opened": cases_opened, "applicants": applicants, "families": families}


async def _waits(
    session: AsyncSession, start: date, end: date, country_id: uuid.UUID | None
) -> tuple[dict[uuid.UUID, list[int]], dict[uuid.UUID, list[int]]]:
    """By visa center: days from the queue to letters of the period, and how many
    applications it took to get the dates of the period."""
    waits: dict[uuid.UUID, list[int]] = defaultdict(list)
    query = (
        select(VisaCase.visa_center_id, WaitingListEntry.letter_on - WaitingListEntry.queued_on)
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(WaitingListEntry.letter_on.between(start, end), VisaCase.visa_center_id.is_not(None))
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    for center, days in await session.execute(query):
        waits[center].append(days)
    attempts: dict[uuid.UUID, list[int]] = defaultdict(list)
    query = (
        select(VisaCase.visa_center_id, ApplicationAttempt.number)
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .where(
            ApplicationAttempt.outcome == AttemptOutcome.DATE_RECEIVED,
            ApplicationAttempt.closed_on.between(start, end),
            VisaCase.visa_center_id.is_not(None),
        )
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    for center, number in await session.execute(query):
        attempts[center].append(number)
    return waits, attempts


async def _centers(
    session: AsyncSession,
    tallies: dict[uuid.UUID, _Tally],
    waits: dict[uuid.UUID, list[int]],
    attempts: dict[uuid.UUID, list[int]],
    country_id: uuid.UUID | None,
) -> list[CenterRow]:
    """Every center with cases, waits or applications in the period, or with a note."""
    notes = dict(
        (await session.execute(select(VisaCenterNote.visa_center_id, VisaCenterNote.note))).all()
    )
    ids = set(tallies) | set(waits) | set(attempts) | set(notes)
    if not ids:
        return []
    query = (
        select(VisaCenter.id, CountryRule.destination_id)
        .join(CountryRule, CountryRule.id == VisaCenter.rule_id)
        .where(VisaCenter.id.in_(ids))
    )
    if country_id is not None:
        query = query.where(CountryRule.destination_id == country_id)
    rows = []
    for center, country in await session.execute(query):
        tally = tallies.get(center, _Tally())
        tried = attempts.get(center, [])
        rows.append(
            CenterRow(
                visa_center_id=center,
                country_id=country,
                submissions=tally.submissions,
                visas=tally.visas,
                refusals=tally.refusals,
                approval=rules.approval(tally.visas, tally.refusals),
                wait_days=rules.average(waits.get(center, [])),
                attempts=round(sum(tried) / len(tried), 1) if tried else None,
                review_days=rules.average(tally.review),
                note=notes.get(center),
            )
        )
    return sorted(rows, key=lambda r: -r.submissions)


async def _queues(
    session: AsyncSession, start: date, end: date, country_id: uuid.UUID | None
) -> list[QueueRow]:
    """Waiting lists by country, visa center and purpose: who waits now, the waits that
    ended with a letter in the period and their spread, who was taken off."""
    waiting = and_(
        WaitingListEntry.letter_on.is_(None),
        WaitingListEntry.removed_on.is_(None),
        VisaCase.stage.not_in(SUBMITTED_STAGES),
    )
    lettered = WaitingListEntry.letter_on.between(start, end)
    removed = WaitingListEntry.removed_on.between(start, end)
    wait = WaitingListEntry.letter_on - WaitingListEntry.queued_on
    query = (
        select(
            VisaCase.destination_id,
            VisaCase.visa_center_id,
            VisaCase.purpose_id,
            func.count().filter(waiting),
            func.count().filter(lettered),
            func.avg(wait).filter(lettered),
            func.min(wait).filter(lettered),
            func.max(wait).filter(lettered),
            func.count().filter(removed),
        )
        .join(VisaCase, VisaCase.id == WaitingListEntry.case_id)
        .where(or_(waiting, lettered, removed))
        .group_by(VisaCase.destination_id, VisaCase.visa_center_id, VisaCase.purpose_id)
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    rows = [
        QueueRow(
            country_id=country,
            visa_center_id=center,
            purpose_id=purpose,
            waiting=now,
            letters=letters,
            average_days=round(avg) if avg is not None else None,
            min_days=low,
            max_days=high,
            removed=gone,
        )
        for country, center, purpose, now, letters, avg, low, high, gone in await session.execute(
            query
        )
    ]
    return sorted(rows, key=lambda r: -(r.average_days or 0))


async def _attempts(
    session: AsyncSession, start: date, end: date, country_id: uuid.UUID | None
) -> list[AttemptRow]:
    """Windowed applications by country: dates got in the period and the attempts it took."""
    query = (
        select(VisaCase.destination_id, func.count(), func.avg(ApplicationAttempt.number))
        .join(VisaCase, VisaCase.id == ApplicationAttempt.case_id)
        .where(
            ApplicationAttempt.outcome == AttemptOutcome.DATE_RECEIVED,
            ApplicationAttempt.closed_on.between(start, end),
        )
        .group_by(VisaCase.destination_id)
    )
    if country_id is not None:
        query = query.where(VisaCase.destination_id == country_id)
    return [
        AttemptRow(country_id=country, dates=count, average_attempts=round(float(avg), 1))
        for country, count, avg in await session.execute(query)
    ]


@router.put("/centers/{center_id}/note", status_code=status.HTTP_204_NO_CONTENT)
async def set_center_note(
    center_id: uuid.UUID, body: NoteIn, user: Member, session: TenantSession
) -> None:
    """The specialist's remark about a visa center; an empty one removes it."""
    if await session.get(VisaCenter, center_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    note = body.note.strip()
    if not note:
        await session.execute(
            delete(VisaCenterNote).where(VisaCenterNote.visa_center_id == center_id)
        )
    else:
        await session.execute(
            insert(VisaCenterNote)
            .values(
                tenant_id=user.tenant_id, visa_center_id=center_id, note=note, updated_by=user.id
            )
            .on_conflict_do_update(
                constraint="uq_visa_center_notes_center",
                set_={"note": note, "updated_by": user.id, "updated_at": func.now()},
            )
        )
    await session.commit()

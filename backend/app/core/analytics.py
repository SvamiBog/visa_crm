"""Analytics rules (backlog 1.13; docs/clients-module.md → «Аналитика»). Pure functions:
the API counts the cases of a period and sorts them with these.

- A case counts in the period of its submission day: the appointment date, or the decision
  day when no date was entered.
- A result is one of: a visa by kind (single, double, multiple by its length), a visa or a
  refusal after an appeal, a refusal, or «не подались» (had a date, did not go).
- Approval is visas out of decisions (visas and refusals), as a percent.
- Profit by month (backlog 1.18; docs/decisions.md → «Ответы визовика (2026-11-26)»): the
  money that came in and went out in the month, each amount on its own day, whatever the
  price of the case: the clients' payments and the consultation credit, less the talon,
  the partner payout marked paid, the tax on the official payments and other expenses.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.core.consultation_checks import add_months
from app.core.finance import ZERO, money, partner_payout, tax, tax_rate_set, to_base
from app.models import PartnerTermsType
from app.models.visa_case import VisaEntries, VisaOutcome

# A multiple visa up to three months, up to a year, or longer (the rest: no dates given).
SHORT_DAYS = 92
YEAR_DAYS = 366


class Result(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    MULTIPLE_SHORT = "multiple_short"
    MULTIPLE_YEAR = "multiple_year"
    MULTIPLE_LONG = "multiple_long"
    MULTIPLE = "multiple"  # validity not entered
    VISA = "visa"  # entries not entered
    VISA_AFTER_APPEAL = "visa_after_appeal"
    REFUSAL = "refusal"
    REFUSAL_AFTER_APPEAL = "refusal_after_appeal"
    NOT_SUBMITTED = "not_submitted"


def result(
    outcome: VisaOutcome,
    *,
    after_appeal: bool = False,
    entries: VisaEntries | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> Result:
    if outcome == VisaOutcome.NOT_SUBMITTED:
        return Result.NOT_SUBMITTED
    if outcome == VisaOutcome.REFUSAL:
        return Result.REFUSAL_AFTER_APPEAL if after_appeal else Result.REFUSAL
    if after_appeal:
        return Result.VISA_AFTER_APPEAL
    match entries:
        case VisaEntries.SINGLE:
            return Result.SINGLE
        case VisaEntries.DOUBLE:
            return Result.DOUBLE
        case VisaEntries.MULTIPLE:
            if valid_from is None or valid_to is None:
                return Result.MULTIPLE
            days = (valid_to - valid_from).days + 1
            if days <= SHORT_DAYS:
                return Result.MULTIPLE_SHORT
            return Result.MULTIPLE_YEAR if days <= YEAR_DAYS else Result.MULTIPLE_LONG
        case _:
            return Result.VISA


def is_visa(r: Result) -> bool:
    return r not in (Result.REFUSAL, Result.REFUSAL_AFTER_APPEAL, Result.NOT_SUBMITTED)


def approval(visas: int, refusals: int) -> int | None:
    """Percent of decisions that were visas; None before any decision."""
    decided = visas + refusals
    return round(visas * 100 / decided) if decided else None


def months(start: date, end: date) -> list[date]:
    """First days of the months the period touches, in order."""
    first, last = start.replace(day=1), end.replace(day=1)
    out = []
    while first <= last:
        out.append(first)
        first = add_months(first, 1)
    return out


def average(values: Iterable[int]) -> int | None:
    values = list(values)
    return round(sum(values) / len(values)) if values else None


# --- Profit by month ------------------------------------------------------------------


@dataclass(frozen=True)
class Payment:
    """A client's payment: its day and the amount in the base currency; `official` — taxed."""

    on: date
    amount_base: Decimal
    official: bool = False


@dataclass(frozen=True)
class Expense:
    """Something the specialist paid for the case: its day, the amount in the base currency."""

    on: date
    amount_base: Decimal


@dataclass(frozen=True)
class CaseMoney:
    """The money of one case, each amount with its day. `price` and `consultation_credit`
    are in the case's currency, `rate` is base units for one unit of it; the talon, the
    partner's fixed payout, payments and expenses are in the base currency.
    `consultation_held_on` is None for a case opened without a consultation, and for one
    whose consultation the reader may not see (an employee given the case of a person
    who was not the one consulted)."""

    opened_on: date  # in the workspace's time zone
    price: Decimal | None = None
    rate: Decimal = Decimal("1")
    consultation_credit: Decimal = ZERO
    consultation_paid_on: date | None = None
    consultation_held_on: date | None = None
    payments: tuple[Payment, ...] = ()
    registrar_fee: Decimal | None = None
    registrar_fee_on: date | None = None
    partner_terms: PartnerTermsType | None = None
    partner_percent: Decimal | None = None
    partner_fixed: Decimal | None = None
    partner_paid_on: date | None = None
    expenses: tuple[Expense, ...] = ()


def credit_day(case: CaseMoney) -> date:
    """The day the consultation credit of a case counts on: when the consultation was
    paid, else when it was held; a credit typed into a case that has no consultation
    counts on the day the case was opened (the owner, 2026-11-26)."""
    return case.consultation_paid_on or case.consultation_held_on or case.opened_on


def profit_by_month(
    cases: Iterable[CaseMoney], tax_rate: Decimal | None, start: date, end: date
) -> dict[date, Decimal]:
    """Profit of every month the period touches (its first day → the base currency):
    what came in on the days of the period less what went out on them, so a month may be
    negative and the months add up to the period. The tax is the rate times the month's
    official payments, rounded once a month; none while the workspace has no rate. A
    payout not marked paid is in no month, and neither is a percent that cannot be
    counted yet (a case without a price)."""
    net = dict.fromkeys(months(start, end), ZERO)
    official = dict.fromkeys(net, ZERO)

    def add(to: dict[date, Decimal], on: date | None, amount: Decimal) -> None:
        # Only the days of the period: the cases come with money of other days too.
        if on is not None and start <= on <= end:
            to[on.replace(day=1)] += amount

    for case in cases:
        for payment in case.payments:
            add(net, payment.on, payment.amount_base)
            if payment.official:
                add(official, payment.on, payment.amount_base)
        add(net, credit_day(case), to_base(case.consultation_credit, case.rate))
        if case.registrar_fee is not None:
            add(net, case.registrar_fee_on, -case.registrar_fee)
        price_base = None if case.price is None else to_base(case.price, case.rate)
        payout = partner_payout(
            case.partner_terms, price_base, case.partner_percent, case.partner_fixed
        )
        if payout is not None:
            add(net, case.partner_paid_on, -payout)
        for expense in case.expenses:
            add(net, expense.on, -expense.amount_base)
    return {
        month: money(
            amount - (tax([official[month]], tax_rate) if tax_rate_set(tax_rate) else ZERO)
        )
        for month, amount in net.items()
    }

"""The specialist's subscription to the CRM (roadmap 3.8, decisions.md → «Слоты и SaaS»):
when the next monthly payment is due and what state the subscription is in. Pure
functions: the API passes in the subscription dates, the months paid so far and today
in the specialist's time zone.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.core.consultation_checks import add_months
from app.core.finance import money

# «Скоро оплата» from this many days before the payment date.
DUE_SOON_DAYS = 7


class Status(StrEnum):
    NONE = "none"  # no tariff assigned
    TRIAL = "trial"
    PAID = "paid"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"


@dataclass(frozen=True)
class Summary:
    status: Status
    next_due_on: date | None
    overdue_days: int


def summarize(
    *, starts_on: date | None, trial_until: date | None, months_paid: int, today: date
) -> Summary:
    """`starts_on` is None when the specialist has no tariff.

    The first payment falls on the end of the trial, or on the start without one; each
    paid month moves the next one a calendar month on, counted from the first payment so
    a late payment does not shift the calendar.
    """
    if starts_on is None:
        return Summary(Status.NONE, None, 0)
    next_due_on = add_months(trial_until or starts_on, months_paid)
    if today > next_due_on:
        return Summary(Status.OVERDUE, next_due_on, (today - next_due_on).days)
    # A payment coming up matters more than the trial it ends.
    if (next_due_on - today).days <= DUE_SOON_DAYS:
        status = Status.DUE_SOON
    elif trial_until is not None and today < trial_until:
        status = Status.TRIAL
    else:
        status = Status.PAID
    return Summary(status, next_due_on, 0)


def amount_due(price: Decimal, months: int) -> Decimal:
    """What a payment for `months` months of a tariff comes to."""
    return money(price * months)

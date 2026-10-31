"""The subscription calendar and states (roadmap 3.8, decisions.md → «Слоты и SaaS»)."""

from datetime import date
from decimal import Decimal
from functools import partial

import pytest

from app.core.subscription import Status, amount_due, summarize


def test_first_payment_is_due_when_the_trial_ends_or_on_the_start_date():
    today = date(2026, 11, 1)
    plain = summarize(starts_on=date(2026, 11, 1), trial_until=None, months_paid=0, today=today)
    trial = summarize(
        starts_on=date(2026, 11, 1), trial_until=date(2026, 11, 15), months_paid=0, today=today
    )
    assert plain.next_due_on == date(2026, 11, 1)
    assert trial.next_due_on == date(2026, 11, 15)


def test_each_paid_month_moves_the_next_payment_one_calendar_month():
    # Counted from the first payment each time: the 31st is not lost after February.
    due = partial(summarize, starts_on=date(2026, 1, 31), trial_until=None, today=date(2026, 1, 1))
    assert [due(months_paid=n).next_due_on for n in range(4)] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    leap = summarize(
        starts_on=date(2028, 1, 31), trial_until=None, months_paid=1, today=date(2028, 1, 1)
    )
    assert leap.next_due_on == date(2028, 2, 29)


@pytest.mark.parametrize(
    ("today", "status", "overdue_days"),
    [
        (date(2026, 11, 7), Status.PAID, 0),  # 8 days before
        (date(2026, 11, 8), Status.DUE_SOON, 0),  # 7 days before
        (date(2026, 11, 15), Status.DUE_SOON, 0),  # the payment date itself
        (date(2026, 11, 16), Status.OVERDUE, 1),
        (date(2026, 12, 1), Status.OVERDUE, 16),
    ],
)
def test_status_is_paid_due_soon_or_overdue(today: date, status: Status, overdue_days: int):
    # Started on 15 October with one month paid: the next payment is on 15 November.
    s = summarize(starts_on=date(2026, 10, 15), trial_until=None, months_paid=1, today=today)
    assert s.next_due_on == date(2026, 11, 15)
    assert (s.status, s.overdue_days) == (status, overdue_days)


def test_trial_lasts_until_its_date_and_the_first_payment_comes_due_after_it():
    in_trial = partial(
        summarize, starts_on=date(2026, 11, 1), trial_until=date(2026, 12, 1), months_paid=0
    )
    assert in_trial(today=date(2026, 11, 2)).status == Status.TRIAL
    # The first payment is close: that matters more than the trial.
    assert in_trial(today=date(2026, 11, 24)).status == Status.DUE_SOON
    assert in_trial(today=date(2026, 12, 2)).status == Status.OVERDUE
    # Paid ahead during the trial: still a trial until it ends, then paid.
    paid_ahead = partial(
        summarize, starts_on=date(2026, 11, 1), trial_until=date(2026, 12, 1), months_paid=1
    )
    assert paid_ahead(today=date(2026, 11, 24)).status == Status.TRIAL
    assert paid_ahead(today=date(2026, 12, 1)).status == Status.PAID


def test_no_tariff_means_no_subscription():
    s = summarize(
        starts_on=None, trial_until=date(2026, 12, 1), months_paid=3, today=date(2027, 6, 1)
    )
    assert (s.status, s.next_due_on, s.overdue_days) == (Status.NONE, None, 0)


def test_amount_due_is_the_price_times_months_in_kopecks():
    assert amount_due(Decimal("49.90"), 3) == Decimal("149.70")
    assert str(amount_due(Decimal("50"), 1)) == "50.00"

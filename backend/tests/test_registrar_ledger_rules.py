"""Settlements with a registrar (docs/clients-module.md → «Расчёты с регистраторами»):
payments add to the balance, talons are charged from it; a positive balance is an unused
advance, a negative one a debt."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal as D

from app.core.registrar_ledger import Movement, build, is_low

T0 = datetime(2026, 5, 1, 9, 0)


def payment(day: int, amount: str, minute: int = 0) -> Movement:
    return Movement(
        "payment", date(2026, 5, day), D(amount), uuid.uuid4(), T0 + timedelta(minutes=minute)
    )


def talon(day: int, amount: str, minute: int = 0) -> Movement:
    return Movement(
        "talon", date(2026, 5, day), D(amount), uuid.uuid4(), T0 + timedelta(minutes=minute)
    )


def test_an_advance_is_spent_talon_by_talon():
    ledger = build([talon(10, "60"), payment(1, "300"), talon(3, "60")])
    assert [line.movement.on.day for line in ledger.lines] == [1, 3, 10]
    assert [line.balance for line in ledger.lines] == [D("300.00"), D("240.00"), D("180.00")]
    assert (ledger.paid, ledger.charged, ledger.balance) == (D("300.00"), D("120.00"), D("180.00"))
    assert ledger.talons == 2


def test_talons_before_a_payment_are_a_debt():
    ledger = build([talon(2, "60"), talon(3, "60.50"), payment(4, "100")])
    assert [line.balance for line in ledger.lines] == [D("-60.00"), D("-120.50"), D("-20.50")]
    assert ledger.balance == D("-20.50")


def test_on_the_same_day_the_payment_comes_first():
    ledger = build([talon(5, "60", minute=1), payment(5, "60", minute=2)])
    assert [line.movement.kind for line in ledger.lines] == ["payment", "talon"]
    assert [line.balance for line in ledger.lines] == [D("60.00"), D("0.00")]
    # Two talons of one day keep the order they were entered in.
    first, second = talon(6, "10", minute=5), talon(6, "20", minute=3)
    assert [line.movement for line in build([first, second]).lines] == [second, first]


def test_an_empty_ledger_is_settled():
    ledger = build([])
    assert (ledger.lines, ledger.balance, ledger.talons) == ([], D("0.00"), 0)


def test_the_advance_runs_low_below_the_threshold():
    assert is_low(D("180"), D("200"))
    assert is_low(D("-20"), D("0"))  # a debt is below any threshold
    assert not is_low(D("200"), D("200"))
    assert not is_low(D("-500"), None)  # no threshold — no reminder

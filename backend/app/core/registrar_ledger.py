"""Settlements with a registrar (docs/clients-module.md → «Расчёты с регистраторами»).

Each registrar keeps one ledger in their own currency: the specialist's payments to them
add to it, the talon of every case they put in a queue is charged from it. The balance
is paid − charged: positive is an advance not spent yet, negative is a debt. When the
advance falls below the registrar's threshold, it is time to pay again.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from app.core.finance import ZERO, money

Kind = Literal["payment", "talon"]
# On one day a payment comes before the talons: an advance paid that day covers them.
_KIND_ORDER = {"payment": 0, "talon": 1}


@dataclass(frozen=True)
class Movement:
    kind: Kind
    on: date
    amount: Decimal  # always positive: a payment adds it, a talon takes it
    ref: uuid.UUID  # the payment, or the case of the talon
    entered_at: datetime  # keeps the order of entries made on one day


@dataclass(frozen=True)
class Line:
    movement: Movement
    balance: Decimal  # after this movement


@dataclass(frozen=True)
class Ledger:
    lines: list[Line]  # oldest first
    paid: Decimal
    charged: Decimal
    balance: Decimal
    talons: int


def build(movements: Iterable[Movement]) -> Ledger:
    ordered = sorted(movements, key=lambda m: (m.on, _KIND_ORDER[m.kind], m.entered_at))
    lines: list[Line] = []
    paid = charged = ZERO
    for movement in ordered:
        if movement.kind == "payment":
            paid += movement.amount
        else:
            charged += movement.amount
        lines.append(Line(movement, money(paid - charged)))
    return Ledger(
        lines=lines,
        paid=money(paid),
        charged=money(charged),
        balance=money(paid - charged),
        talons=sum(1 for m in ordered if m.kind == "talon"),
    )


def is_low(balance: Decimal, threshold: Decimal | None) -> bool:
    """The advance is running out (a debt counts too) — only when a threshold is set."""
    return threshold is not None and balance < threshold

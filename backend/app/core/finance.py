"""Case finance formulas (docs/clients-module.md → «Финансы», decisions.md → «Деньги»).

Money is Decimal, never float, rounded to kopecks half up. The price of a case (with its
prepayment and consultation credit) is in the case's own currency, which the specialist
picks among the ones she accepts (pilot fix 4, 2026-11-19); the case keeps the rate of
that currency to the base one. Payments, expenses and the talon keep their amount in the
base currency, converted once when entered.

- Balance, in the case's currency = price − consultation credit − payments; a payment in
  another currency counts at its base amount over the case's rate; rounded once.
- Partner payout: a percent of the whole price in the base currency (no deductions), a
  fixed amount, nothing, or not tracked at all («не указывать»: neither counted nor stored).
- Tax (НПД) = rate × payments marked official, in the base currency; no tax while the
  workspace has no rate (pilot fix 5). A rate of 0 is her explicit «no tax» (pilot T8).
- Profit, in the base currency = price − talon − partner payout − tax − other expenses.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeGuard

from app.models import PartnerTermsType

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def to_base(amount: Decimal, rate: Decimal) -> Decimal:
    """An amount in the base currency; `rate` is base units for one unit of the currency."""
    return money(amount * rate)


def partner_payout(
    terms: PartnerTermsType | None,
    price: Decimal | None,
    percent: Decimal | None,
    fixed: Decimal | None,
) -> Decimal | None:
    """None when the payout is not tracked or cannot be counted yet."""
    match terms:
        case PartnerTermsType.PERCENT:
            return None if price is None or percent is None else money(price * percent / 100)
        case PartnerTermsType.FIXED:
            return None if fixed is None else money(fixed)
        case PartnerTermsType.NONE:
            return ZERO
        case _:
            return None


def tax(official_payments: Iterable[Decimal], rate_percent: Decimal) -> Decimal:
    return money(sum(official_payments, ZERO) * rate_percent / 100)


def balance(
    price: Decimal | None, consultation_credit: Decimal, payments: Iterable[Decimal]
) -> Decimal | None:
    """What the client still owes; negative when they paid more."""
    if price is None:
        return None
    return money(price - consultation_credit - sum(payments, ZERO))


def profit(
    price: Decimal | None,
    registrar_fee: Decimal | None,
    payout: Decimal | None,
    tax_amount: Decimal,
    expenses: Iterable[Decimal],
) -> Decimal | None:
    if price is None:
        return None
    costs = (registrar_fee or ZERO) + (payout or ZERO) + tax_amount + sum(expenses, ZERO)
    return money(price - costs)


@dataclass(frozen=True)
class Paid:
    """A client's payment: its amount and currency, and the amount in the base currency."""

    amount: Decimal
    currency: str
    amount_base: Decimal
    official: bool = False


def in_case_currency(paid: Paid, currency: str, rate: Decimal) -> Decimal:
    """A payment in the case's currency, not rounded: a payment in that currency counts as
    it is, any other one is its base amount at the case's rate (`rate`: base units for one
    unit of the case's currency), so the conversion goes through the base currency."""
    if paid.currency == currency:
        return paid.amount
    return paid.amount_base / rate


def tax_rate_set(rate: Decimal | None) -> TypeGuard[Decimal]:
    """None: the specialist has not set her rate yet; 0 is a rate she chose, no tax."""
    return rate is not None


@dataclass(frozen=True)
class Summary:
    """`paid` and `balance` in the case's currency, the rest in the base currency; `tax`
    is None while the workspace has no tax rate."""

    paid: Decimal
    official: Decimal
    balance: Decimal | None
    balance_base: Decimal | None
    price_base: Decimal | None
    partner_payout: Decimal | None
    tax: Decimal | None
    expenses: Decimal
    profit: Decimal | None


def summarize(
    *,
    price: Decimal | None,
    currency: str,
    rate: Decimal,
    consultation_credit: Decimal,
    payments: list[Paid],
    registrar_fee: Decimal | None,
    partner_terms: PartnerTermsType | None,
    partner_percent: Decimal | None,
    partner_fixed: Decimal | None,
    tax_rate: Decimal | None,
    expenses: list[Decimal],
) -> Summary:
    """`price` and `consultation_credit` in the case's `currency`, `rate` base units for
    one unit of it (1 when it is the base currency)."""
    paid = sum((in_case_currency(p, currency, rate) for p in payments), ZERO)
    official = [p.amount_base for p in payments if p.official]
    price_base = None if price is None else to_base(price, rate)
    payout = partner_payout(partner_terms, price_base, partner_percent, partner_fixed)
    tax_amount = tax(official, tax_rate) if tax_rate_set(tax_rate) else None
    # Rounded once: not payment by payment.
    left = None if price is None else price - consultation_credit - paid
    return Summary(
        paid=money(paid),
        official=money(sum(official, ZERO)),
        balance=None if left is None else money(left),
        balance_base=None if left is None else money(left * rate),
        price_base=price_base,
        partner_payout=payout,
        tax=tax_amount,
        expenses=money(sum(expenses, ZERO)),
        profit=profit(price_base, registrar_fee, payout, tax_amount or ZERO, expenses),
    )

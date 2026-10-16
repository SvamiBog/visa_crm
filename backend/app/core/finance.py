"""Case finance formulas (docs/clients-module.md → «Финансы», decisions.md → «Деньги»).

Everything is counted in the specialist's base currency (BYN for now) with Decimal, never
float. A foreign amount is converted once, when it is entered, and rounded to kopecks
half up; the sums below add those converted amounts.

- Balance = price − consultation credit − payments.
- Partner payout: a percent of the whole price (no deductions), a fixed amount, nothing,
  or not tracked at all («не указывать»: neither counted nor stored).
- Tax (НПД) = rate × payments marked official.
- Profit = price − talon − partner payout − tax − other expenses.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

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
class Summary:
    paid: Decimal
    official: Decimal
    balance: Decimal | None
    partner_payout: Decimal | None
    tax: Decimal
    expenses: Decimal
    profit: Decimal | None


def summarize(
    *,
    price: Decimal | None,
    consultation_credit: Decimal,
    payments: list[tuple[Decimal, bool]],
    registrar_fee: Decimal | None,
    partner_terms: PartnerTermsType | None,
    partner_percent: Decimal | None,
    partner_fixed: Decimal | None,
    tax_rate: Decimal,
    expenses: list[Decimal],
) -> Summary:
    """`payments`: (amount in the base currency, paid officially)."""
    amounts = [amount for amount, _ in payments]
    official = [amount for amount, is_official in payments if is_official]
    payout = partner_payout(partner_terms, price, partner_percent, partner_fixed)
    tax_amount = tax(official, tax_rate)
    return Summary(
        paid=money(sum(amounts, ZERO)),
        official=money(sum(official, ZERO)),
        balance=balance(price, consultation_credit, amounts),
        partner_payout=payout,
        tax=tax_amount,
        expenses=money(sum(expenses, ZERO)),
        profit=profit(price, registrar_fee, payout, tax_amount, expenses),
    )

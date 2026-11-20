"""Changing the specialist's base currency (pilot T8, owner's answers of 2026-11-20;
docs/decisions.md → «Пилот: правки визовика», docs/clients-module.md → «Финансы»).

She picks the base currency herself in «Настройки», among the ones she accepts (the admin
may change it too). Everything she entered keeps its amount and currency: a payment, an
expense, a talon, the price of a case. Counted again is only what is kept in the base
currency, from the original amount and currency at the site's latest National Bank rate
(through BYN, app/core/rates.py), rounded once (finance.money):

- the rate and the amount in the base currency of a payment, an expense and a talon; a row
  in the new base is at 1. A rate she corrected by hand was to the old base: the bank's
  replaces it, and she may correct it again;
- the rate of a case (base units for one unit of its price's currency); 1 for a case
  priced in the new base. The price, the prepayment and the consultation credit stay;
- amounts kept in the base currency with no currency of their own (a partner's fixed
  payout in a case, the prepayments list) are converted from the old base to the new.

Not the specialist's base, so left as they are: the subscription (the platform's
currency), the registrars' settlements and prices, partners' terms and consultation fees
(each in its own currency).

All of it in the caller's one transaction, so the change is whole or nothing: every rate
it needs is read before a row changes, and a rate the site lacks (RateMissing) stops it.
An import writing now (it holds the old base) refuses the change; a sample or a check of
a file, counted in the old base, is stopped.
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import import_jobs
from app.core.finance import money, to_base
from app.core.money import CENT, ordered_currencies
from app.core.rates import RateUnavailable, byn_rate, cross_rate
from app.models import CaseExpense, CaseFinance, CasePayment, Prepayment, Tenant


class RateMissing(Exception):
    """The site has no National Bank rate of a currency the change needs: nothing changed."""

    def __init__(self, currency: str) -> None:
        super().__init__(currency)
        self.currency = currency


def rebased(
    amount: Decimal, currency: str, base: str, byn: Mapping[str, Decimal]
) -> tuple[Decimal, Decimal]:
    """A row's rate to the new `base` and its amount in it, from its own amount and
    currency: the stored rate (six places) times the amount, rounded once."""
    rate = cross_rate(currency, base, byn)
    return rate, to_base(amount, rate)


def converted(amount: Decimal, old: str, new: str, byn: Mapping[str, Decimal]) -> Decimal:
    """An amount kept in the old base currency, in the new one: rounded once, with no
    rate rounded on the way (no row keeps one for it)."""
    if old == new:
        return money(amount)
    in_byn = amount if old == "BYN" else amount * byn[old]
    return money(in_byn if new == "BYN" else in_byn / byn[new])


@dataclass(frozen=True)
class Recounted:
    """How many rows of each kind were counted again: the audit record's details."""

    payments: int = 0
    expenses: int = 0
    talons: int = 0
    cases: int = 0
    partner_payouts: int = 0
    prepayments: int = 0

    def details(self, old: str, new: str) -> dict[str, Any]:
        return {"from": old, "to": new} | asdict(self)


async def _byn_rates(currencies: Iterable[str], base: str) -> dict[str, Decimal]:
    """The site's BYN rates the conversions into `base` need; RateMissing if one is not
    there (the bank is never asked from here: app/core/rates.py)."""
    needed = {c for c in currencies if c != base}
    if needed:
        needed.add(base)
    out: dict[str, Decimal] = {}
    for currency in sorted(needed - {"BYN"}):
        try:
            out[currency] = await byn_rate(currency)
        except RateUnavailable as error:
            raise RateMissing(currency) from error
    return out


async def change_base_currency(session: AsyncSession, tenant: Tenant, new: str) -> Recounted:
    """Make `new` the workspace's base currency and count again what is kept in the base
    one, in the caller's transaction (it records the audit event and commits). The caller
    holds the workspace's row locked. Raises ImportRunning or RateMissing before anything
    changes."""
    old = tenant.base_currency
    if new == old:
        return Recounted()
    if any(job.kind == "import" for job in import_jobs.running(tenant.id)):
        raise import_jobs.ImportRunning
    # By tenant_id as well as by RLS: the admin's system scope sees every workspace.
    tid = tenant.id
    payments = list(await session.scalars(select(CasePayment).where(CasePayment.tenant_id == tid)))
    expenses = list(await session.scalars(select(CaseExpense).where(CaseExpense.tenant_id == tid)))
    finances = list(await session.scalars(select(CaseFinance).where(CaseFinance.tenant_id == tid)))
    prepayments = list(await session.scalars(select(Prepayment).where(Prepayment.tenant_id == tid)))
    talons = [f for f in finances if f.registrar_fee_amount is not None]
    payouts = [f for f in finances if f.partner_fixed is not None]

    own = {r.currency for r in (*payments, *expenses, *finances)}
    own |= {f.registrar_fee_currency for f in talons if f.registrar_fee_currency is not None}
    kept_in_base = bool(payouts or prepayments)
    byn = await _byn_rates(own | ({old} if kept_in_base else set()), new)

    # Nothing has changed so far: from here on it cannot fail for a missing rate.
    import_jobs.stop(tenant.id)
    for row in (*payments, *expenses):
        row.rate, row.amount_base = rebased(row.amount, row.currency, new, byn)
    for f in finances:
        f.rate = cross_rate(f.currency, new, byn)
    for f in talons:
        assert f.registrar_fee_amount is not None and f.registrar_fee_currency is not None
        f.registrar_fee_rate, f.registrar_fee_base = rebased(
            f.registrar_fee_amount, f.registrar_fee_currency, new, byn
        )
    # Both must stay positive (the API takes no 0): a cent at least, for a cent converted.
    for f in payouts:
        assert f.partner_fixed is not None
        f.partner_fixed = max(converted(f.partner_fixed, old, new, byn), CENT)
    for p in prepayments:
        p.amount = max(converted(p.amount, old, new, byn), CENT)
    # The old base stays accepted: the payments she took in it are still there.
    tenant.payment_currencies = ordered_currencies(new, [*tenant.payment_currencies, old])
    tenant.base_currency = new
    await session.flush()
    return Recounted(
        payments=len(payments),
        expenses=len(expenses),
        talons=len(talons),
        cases=len(finances),
        partner_payouts=len(payouts),
        prepayments=len(prepayments),
    )

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
- a partner's fixed payout in a case, kept in the base currency with no currency of its
  own, is converted from the old base to the new; a cent stays a cent at least.

Not the specialist's base, so left as they are: the subscription (the platform's
currency), the registrars' settlements and prices, partners' terms, consultation fees and
the prepayments list (each in its own currency, owner's answer of 2026-11-20).

All of it in the caller's one transaction, so the change is whole or nothing: every rate
it needs is read and every new amount counted before a row changes; a rate the site lacks
(RateMissing) or an amount the new base cannot keep (AmountTooLarge) stops it.
An import writing now (it holds the old base) refuses the change; a sample or a check of
a file, counted in the old base, is stopped.

A write that counts an amount in the base currency takes the workspace's row FOR KEY SHARE
first (`hold_base`); the change takes it FOR UPDATE, which waits for those writes and
makes new ones wait, so none of them keeps a rate to the old base. Other changes of the
row (the tax rate, the reminder windows: a plain UPDATE) conflict with neither.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import import_jobs
from app.core.finance import check_case_amounts, converted, storable, stored_base
from app.core.money import CENT, ordered_currencies
from app.core.rates import RateUnavailable, byn_rate, cross_rate
from app.models import CaseExpense, CaseFinance, CasePayment, Tenant


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
    return rate, stored_base(amount, rate)


@dataclass(frozen=True)
class Recounted:
    """How many rows of each kind were counted again: the audit record's details."""

    payments: int = 0
    expenses: int = 0
    talons: int = 0
    cases: int = 0
    partner_payouts: int = 0

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


async def hold_base(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """The workspace for a write that counts an amount in its base currency (a payment, an
    expense, a talon, a case's rate, an import), its row held FOR KEY SHARE until the
    transaction ends: a change of the base currency waits for the write, and a write that
    comes during a change waits for it and reads the new base."""
    tenant = await session.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    )
    if tenant is None:
        raise LookupError(tenant_id)
    return tenant


async def change_base_currency(session: AsyncSession, tenant: Tenant, new: str) -> Recounted:
    """Make `new` the workspace's base currency and count again what is kept in the base
    one, in the caller's transaction (it records the audit event and commits). The caller
    holds the workspace's row FOR NO KEY UPDATE, so no other change of the settings runs at
    once. Raises ImportRunning, RateMissing or AmountTooLarge before anything changes."""
    old = tenant.base_currency
    if new == old:
        return Recounted()
    tid = tenant.id
    # A sample or a check counts in the old base: it stops (and lets the row go). An
    # import writing now refuses the change (ImportRunning).
    import_jobs.stop(tid)
    # FOR UPDATE: waits for the writes holding the row (hold_base) and for the inserts of
    # this workspace; the rows read below are then all there are.
    await session.execute(select(Tenant.id).where(Tenant.id == tid).with_for_update())
    # By tenant_id as well as by RLS: the admin's system scope sees every workspace.
    payments = list(await session.scalars(select(CasePayment).where(CasePayment.tenant_id == tid)))
    expenses = list(await session.scalars(select(CaseExpense).where(CaseExpense.tenant_id == tid)))
    finances = list(await session.scalars(select(CaseFinance).where(CaseFinance.tenant_id == tid)))
    talons = [f for f in finances if f.registrar_fee_amount is not None]
    payouts = [f for f in finances if f.partner_fixed is not None]

    own = {r.currency for r in (*payments, *expenses, *finances)}
    own |= {f.registrar_fee_currency for f in talons if f.registrar_fee_currency is not None}
    byn = await _byn_rates(own | ({old} if payouts else set()), new)

    # Every new value is counted before a row changes: an amount the new base cannot keep
    # (AmountTooLarge, audit H1) stops the change like a missing rate.
    moneys = [(row, rebased(row.amount, row.currency, new, byn)) for row in (*payments, *expenses)]
    case_rates = [(f, cross_rate(f.currency, new, byn)) for f in finances]
    for f, rate in case_rates:
        check_case_amounts(rate, f.price, f.prepayment, f.consultation_credit)
    fees = []
    for f in talons:
        assert f.registrar_fee_amount is not None and f.registrar_fee_currency is not None
        fees.append((f, rebased(f.registrar_fee_amount, f.registrar_fee_currency, new, byn)))
    # A payout of 0 would read as nothing to pay (the API takes no 0): a cent at least.
    fixed = []
    for f in payouts:
        assert f.partner_fixed is not None
        fixed.append((f, max(storable(converted(f.partner_fixed, old, new, byn)), CENT)))

    for row, (rate, base) in moneys:
        row.rate, row.amount_base = rate, base
    for f, rate in case_rates:
        f.rate = rate
    for f, (rate, base) in fees:
        f.registrar_fee_rate, f.registrar_fee_base = rate, base
    for f, payout in fixed:
        f.partner_fixed = payout
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
    )

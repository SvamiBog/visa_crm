"""Case finances (backlog 1.9): the price, the client's payments and balance, the talon,
the partner payout, the tax (НПД), other expenses and the profit of a case, in the
specialist's base currency (formulas: app/core/finance.py).

Closed by the «clients», «cases» and «finances» modules: an employee sees money only
when the specialist gives them «Финансы кейсов». Events carry ids and field names, no
amounts.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.finance import Summary, summarize, to_base
from app.core.money import Amount, Currency, Percent
from app.core.rates import RateUnavailable, rate_to_base
from app.models import (
    AuditAction,
    CaseExpense,
    CaseFinance,
    CasePayment,
    Consultation,
    Contact,
    PartnerTerms,
    PartnerTermsType,
    Prepayment,
    RegistrarPrice,
    Tenant,
    VisaCase,
    WaitingListEntry,
)
from app.models.dictionaries import Hall

router = APIRouter(
    tags=["finances"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("cases")),
        Depends(require_module("finances")),
    ],
)

Member = Annotated[CurrentUser, Depends(require_module("finances"))]

# Base units for one unit of a currency: up to 6 places, like the National Bank's.
Rate = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=6)]
Price = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Note = Annotated[str, Field(max_length=200)]
FINANCE_FIELDS = (
    "price",
    "prepayment",
    "consultation_credit",
    "registrar_fee_amount",
    "registrar_fee_currency",
    "registrar_fee_rate",
    "registrar_fee_on",
    "partner_terms",
    "partner_percent",
    "partner_fixed",
    "partner_paid_on",
)


# --- Schemas -------------------------------------------------------------------------


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class FinanceUpdate(BaseModel):
    """Only the fields sent are changed; null clears one. The talon is sent whole
    (amount, currency and rate) or cleared with a null amount. Its day (when it is
    charged in the settlements with the registrar) defaults to the day the client was
    queued, else today; a rate not sent is the bank's of that day."""

    price: Price | None = None
    prepayment: Amount | None = None
    consultation_credit: Price | None = None
    registrar_fee_amount: Amount | None = None
    registrar_fee_currency: Currency | None = None
    registrar_fee_rate: Rate | None = None
    registrar_fee_on: date | None = None
    partner_terms: PartnerTermsType | None = None
    partner_percent: Percent | None = None
    partner_fixed: Amount | None = None
    partner_paid_on: date | None = None


class MoneyIn(BaseModel):
    amount: Amount
    currency: Currency
    # Omitted: the National Bank's rate for the day.
    rate: Rate | None = None


class PaymentIn(MoneyIn):
    paid_on: date
    official: bool = False
    note: Note | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class PaymentUpdate(BaseModel):
    paid_on: date | None = None
    amount: Amount | None = None
    currency: Currency | None = None
    rate: Rate | None = None
    official: bool | None = None
    note: Note | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class ExpenseIn(MoneyIn):
    spent_on: date
    description: Annotated[str, Field(min_length=1, max_length=200)]

    _description = field_validator("description", mode="before")(_strip)


class ExpenseUpdate(BaseModel):
    spent_on: date | None = None
    amount: Amount | None = None
    currency: Currency | None = None
    rate: Rate | None = None
    description: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    _description = field_validator("description", mode="before")(_strip)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    paid_on: date
    amount: Decimal
    currency: str
    rate: Decimal
    amount_base: Decimal
    official: bool
    note: str | None


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    spent_on: date
    amount: Decimal
    currency: str
    rate: Decimal
    amount_base: Decimal
    description: str


class FeeSuggestion(BaseModel):
    amount: Decimal
    currency: str
    # The day it is charged: when the client was queued, else today.
    on: date
    # Base units for one unit that day; None when the bank's rate cannot be had.
    rate: Decimal | None


class Suggestions(BaseModel):
    """What the lists and the consultation suggest, to fill an empty field."""

    prepayment: Decimal | None = None
    registrar_fee: FeeSuggestion | None = None
    partner_terms: PartnerTermsType | None = None
    partner_percent: Decimal | None = None
    partner_fixed: Decimal | None = None
    consultation_credit: Decimal | None = None


class FinanceOut(BaseModel):
    case_id: uuid.UUID
    base_currency: str
    tax_rate: Decimal
    price: Decimal | None = None
    prepayment: Decimal | None = None
    consultation_credit: Decimal = Decimal("0.00")
    registrar_fee_amount: Decimal | None = None
    registrar_fee_currency: str | None = None
    registrar_fee_rate: Decimal | None = None
    registrar_fee_base: Decimal | None = None
    registrar_fee_on: date | None = None
    partner_terms: PartnerTermsType | None = None
    partner_percent: Decimal | None = None
    partner_fixed: Decimal | None = None
    partner_paid_on: date | None = None
    payments: list[PaymentOut] = []
    expenses: list[ExpenseOut] = []
    # Counted: what was paid, of it officially, the balance, the payout, tax, profit.
    paid: Decimal
    official: Decimal
    balance: Decimal | None
    partner_payout: Decimal | None
    tax: Decimal
    expenses_total: Decimal
    profit: Decimal | None
    suggestions: Suggestions


class RateOut(BaseModel):
    currency: str
    base_currency: str
    on: date
    rate: Decimal


# --- Helpers -------------------------------------------------------------------------


async def _get[T](session: AsyncSession, model: type[T], item_id: Any) -> T:
    """RLS hides other workspaces' rows, so someone else's id is simply not found."""
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


async def _tenant(session: AsyncSession, user: CurrentUser) -> Tenant:
    return await _get(session, Tenant, user.tenant_id)


async def _rate(currency: str, base: str, day: date, given: Decimal | None) -> Decimal:
    if currency == base:
        return Decimal("1")
    if given is not None:
        return given
    try:
        return await rate_to_base(currency, base, day)
    except RateUnavailable as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "rate_unavailable") from error


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    case: VisaCase,
    target: tuple[str, uuid.UUID] | None = None,
    fields: list[str] | None = None,
) -> None:
    """Ids and field names only: no amounts."""
    target_type, target_id = target or ("case", case.id)
    details: dict[str, Any] = {"case": str(case.id), "contact": str(case.contact_id)}
    if fields:
        details["fields"] = ",".join(sorted(fields))
    record(
        session, action, actor=user, target_type=target_type, target_id=target_id, details=details
    )


async def registrar_price_for(session: AsyncSession, case: VisaCase) -> RegistrarPrice | None:
    """The talon of the case's registrar for its country, hall and purpose (the purpose's
    own price wins over «any purpose»)."""
    if case.registrar_id is None:
        return None
    prices = {
        p.purpose_id: p
        for p in await session.scalars(
            select(RegistrarPrice).where(
                RegistrarPrice.registrar_id == case.registrar_id,
                RegistrarPrice.country_id == case.destination_id,
                RegistrarPrice.hall == (case.hall or Hall.STANDARD),
            )
        )
    }
    return prices.get(case.purpose_id, prices.get(None))


async def first_queued_on(session: AsyncSession, case_id: uuid.UUID) -> date | None:
    """When the client was first put in a waiting list: the day the talon is charged."""
    return await session.scalar(
        select(func.min(WaitingListEntry.queued_on)).where(WaitingListEntry.case_id == case_id)
    )


async def _suggestions(
    session: AsyncSession, case: VisaCase, tenant: Tenant, today: date
) -> Suggestions:
    out = Suggestions()
    prepayments = {
        p.purpose_id: p.amount
        for p in await session.scalars(
            select(Prepayment).where(Prepayment.country_id == case.destination_id)
        )
    }
    out.prepayment = prepayments.get(case.purpose_id, prepayments.get(None))

    if case.registrar_id is not None:
        price = await registrar_price_for(session, case)
        if price is not None:
            day = await first_queued_on(session, case.id) or today
            try:
                rate = await rate_to_base(price.currency, tenant.base_currency, day)
            except RateUnavailable:
                rate = None
            out.registrar_fee = FeeSuggestion(
                amount=price.amount, currency=price.currency, on=day, rate=rate
            )

    contact = await _get(session, Contact, case.contact_id)
    if contact.partner_id is not None:
        terms = {
            t.country_id: t
            for t in await session.scalars(
                select(PartnerTerms).where(PartnerTerms.partner_id == contact.partner_id)
            )
        }
        chosen = terms.get(case.destination_id, terms.get(None))
        if chosen is not None:
            out.partner_terms = chosen.terms_type
            out.partner_percent = chosen.percent
            if chosen.amount is not None and chosen.currency is not None:
                try:
                    rate = await rate_to_base(chosen.currency, tenant.base_currency, today)
                    out.partner_fixed = to_base(chosen.amount, rate)
                except RateUnavailable:
                    out.partner_fixed = None

    # The consultation fee counts once: towards the case of the person consulted.
    if case.consultation_id is not None:
        consultation = await session.get(Consultation, case.consultation_id)
        if (
            consultation is not None
            and consultation.contact_id == case.contact_id
            and consultation.fee_paid
            and consultation.fee_amount is not None
            and consultation.fee_currency is not None
        ):
            try:
                rate = await rate_to_base(
                    consultation.fee_currency,
                    tenant.base_currency,
                    consultation.fee_paid_on or consultation.held_on,
                )
                out.consultation_credit = to_base(consultation.fee_amount, rate)
            except RateUnavailable:
                out.consultation_credit = None
    return out


async def finance_out(session: AsyncSession, case: VisaCase, user: CurrentUser) -> FinanceOut:
    tenant = await _tenant(session, user)
    finance = await session.get(CaseFinance, case.id)
    payments = list(
        await session.scalars(
            select(CasePayment)
            .where(CasePayment.case_id == case.id)
            .order_by(CasePayment.paid_on, CasePayment.created_at)
        )
    )
    expenses = list(
        await session.scalars(
            select(CaseExpense)
            .where(CaseExpense.case_id == case.id)
            .order_by(CaseExpense.spent_on, CaseExpense.created_at)
        )
    )
    summary: Summary = summarize(
        price=finance.price if finance else None,
        consultation_credit=finance.consultation_credit if finance else Decimal("0"),
        payments=[(p.amount_base, p.official) for p in payments],
        registrar_fee=finance.registrar_fee_base if finance else None,
        partner_terms=finance.partner_terms if finance else None,
        partner_percent=finance.partner_percent if finance else None,
        partner_fixed=finance.partner_fixed if finance else None,
        tax_rate=tenant.tax_rate,
        expenses=[e.amount_base for e in expenses],
    )
    stored = (
        {f: getattr(finance, f) for f in (*FINANCE_FIELDS, "registrar_fee_base")} if finance else {}
    )
    return FinanceOut(
        case_id=case.id,
        base_currency=tenant.base_currency,
        tax_rate=tenant.tax_rate,
        **stored,
        payments=[PaymentOut.model_validate(p) for p in payments],
        expenses=[ExpenseOut.model_validate(e) for e in expenses],
        paid=summary.paid,
        official=summary.official,
        balance=summary.balance,
        partner_payout=summary.partner_payout,
        tax=summary.tax,
        expenses_total=summary.expenses,
        profit=summary.profit,
        suggestions=await _suggestions(
            session, case, tenant, await workspace_today(session, tenant.id)
        ),
    )


def _check_finance(finance: CaseFinance) -> None:
    fee = (
        finance.registrar_fee_amount,
        finance.registrar_fee_currency,
        finance.registrar_fee_rate,
        finance.registrar_fee_on,
    )
    if any(v is None for v in fee) and any(v is not None for v in fee):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "registrar_fee_incomplete")
    if finance.partner_terms != PartnerTermsType.PERCENT:
        finance.partner_percent = None
    if finance.partner_terms != PartnerTermsType.FIXED:
        finance.partner_fixed = None
    if finance.partner_terms not in (PartnerTermsType.PERCENT, PartnerTermsType.FIXED):
        finance.partner_paid_on = None
    if finance.partner_terms == PartnerTermsType.PERCENT and finance.partner_percent is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "partner_terms_incomplete")
    if finance.partner_terms == PartnerTermsType.FIXED and finance.partner_fixed is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "partner_terms_incomplete")


# --- Endpoints -----------------------------------------------------------------------


@router.get("/rates")
async def exchange_rate(
    user: Member,
    session: TenantSession,
    currency: Annotated[Currency, Query()],
    on: Annotated[date, Query()],
) -> RateOut:
    """The National Bank's rate for the day, in the specialist's base currency."""
    tenant = await _tenant(session, user)
    return RateOut(
        currency=currency,
        base_currency=tenant.base_currency,
        on=on,
        rate=await _rate(currency, tenant.base_currency, on, None),
    )


@router.get("/cases/{case_id}/finance")
async def get_finance(case_id: uuid.UUID, user: Member, session: TenantSession) -> FinanceOut:
    return await finance_out(session, await _get(session, VisaCase, case_id), user)


@router.patch("/cases/{case_id}/finance")
async def update_finance(
    case_id: uuid.UUID, body: FinanceUpdate, user: Member, session: TenantSession
) -> FinanceOut:
    case = await _get(session, VisaCase, case_id)
    tenant = await _tenant(session, user)
    finance = await session.get(CaseFinance, case.id)
    if finance is None:
        finance = CaseFinance(case_id=case.id, tenant_id=case.tenant_id)
        session.add(finance)
    changes = body.model_dump(exclude_unset=True)
    if "consultation_credit" in changes and changes["consultation_credit"] is None:
        changes["consultation_credit"] = Decimal("0")
    if "registrar_fee_amount" in changes and changes["registrar_fee_amount"] is None:
        changes.update(registrar_fee_currency=None, registrar_fee_rate=None, registrar_fee_on=None)
    else:
        if (
            changes.get("registrar_fee_amount") is not None
            and finance.registrar_fee_on is None
            and changes.get("registrar_fee_on") is None
        ):
            # A new talon: charged the day the client was queued, else today.
            changes["registrar_fee_on"] = await first_queued_on(
                session, case.id
            ) or await workspace_today(session, tenant.id)
        currency = changes.get("registrar_fee_currency")
        if currency == tenant.base_currency:
            changes["registrar_fee_rate"] = Decimal("1")
        elif currency is not None and "registrar_fee_rate" not in changes:
            # A new currency without a rate: the bank's rate of the talon's day.
            day = changes.get("registrar_fee_on") or finance.registrar_fee_on
            day = day or await workspace_today(session, tenant.id)
            changes["registrar_fee_rate"] = await _rate(currency, tenant.base_currency, day, None)
    changed = [f for f, v in changes.items() if getattr(finance, f) != v]
    for field, value in changes.items():
        setattr(finance, field, value)
    _check_finance(finance)
    finance.registrar_fee_base = (
        to_base(finance.registrar_fee_amount, finance.registrar_fee_rate)
        if finance.registrar_fee_amount is not None and finance.registrar_fee_rate is not None
        else None
    )
    if changed:
        _audit(session, AuditAction.FINANCE_UPDATED, user, case, fields=changed)
    await session.commit()
    return await finance_out(session, case, user)


@router.post("/cases/{case_id}/payments", status_code=status.HTTP_201_CREATED)
async def add_payment(
    case_id: uuid.UUID, body: PaymentIn, user: Member, session: TenantSession
) -> FinanceOut:
    case = await _get(session, VisaCase, case_id)
    tenant = await _tenant(session, user)
    rate = await _rate(body.currency, tenant.base_currency, body.paid_on, body.rate)
    payment = CasePayment(
        tenant_id=case.tenant_id,
        case_id=case.id,
        created_by=user.id,
        **body.model_dump(exclude={"rate"}),
        rate=rate,
        amount_base=to_base(body.amount, rate),
    )
    session.add(payment)
    await session.flush()
    _audit(session, AuditAction.PAYMENT_ADDED, user, case, ("payment", payment.id))
    await session.commit()
    return await finance_out(session, case, user)


async def _update_money(
    session: AsyncSession,
    user: CurrentUser,
    item: CasePayment | CaseExpense,
    changes: dict[str, Any],
    day_field: str,
) -> list[str]:
    """Apply the changes; a new currency or day takes the bank's rate unless one is given."""
    tenant = await _tenant(session, user)
    for field in ("amount", "currency", day_field):
        if field in changes and changes[field] is None:
            del changes[field]  # required: null means «leave as is»
    changed = [f for f, v in changes.items() if getattr(item, f) != v]
    new_rate = changes.pop("rate", None)
    for field, value in changes.items():
        setattr(item, field, value)
    if new_rate is not None or "currency" in changed or day_field in changed:
        day = getattr(item, day_field)
        item.rate = await _rate(item.currency, tenant.base_currency, day, new_rate)
    item.amount_base = to_base(item.amount, item.rate)
    return changed


@router.patch("/payments/{payment_id}")
async def update_payment(
    payment_id: uuid.UUID, body: PaymentUpdate, user: Member, session: TenantSession
) -> FinanceOut:
    payment = await _get(session, CasePayment, payment_id)
    case = await _get(session, VisaCase, payment.case_id)
    changed = await _update_money(
        session, user, payment, body.model_dump(exclude_unset=True), "paid_on"
    )
    if changed:
        _audit(session, AuditAction.PAYMENT_UPDATED, user, case, ("payment", payment.id), changed)
    await session.commit()
    return await finance_out(session, case, user)


@router.delete("/payments/{payment_id}")
async def delete_payment(payment_id: uuid.UUID, user: Member, session: TenantSession) -> FinanceOut:
    payment = await _get(session, CasePayment, payment_id)
    case = await _get(session, VisaCase, payment.case_id)
    _audit(session, AuditAction.PAYMENT_DELETED, user, case, ("payment", payment.id))
    await session.delete(payment)
    await session.commit()
    return await finance_out(session, case, user)


@router.post("/cases/{case_id}/expenses", status_code=status.HTTP_201_CREATED)
async def add_expense(
    case_id: uuid.UUID, body: ExpenseIn, user: Member, session: TenantSession
) -> FinanceOut:
    case = await _get(session, VisaCase, case_id)
    tenant = await _tenant(session, user)
    rate = await _rate(body.currency, tenant.base_currency, body.spent_on, body.rate)
    expense = CaseExpense(
        tenant_id=case.tenant_id,
        case_id=case.id,
        created_by=user.id,
        **body.model_dump(exclude={"rate"}),
        rate=rate,
        amount_base=to_base(body.amount, rate),
    )
    session.add(expense)
    await session.flush()
    _audit(session, AuditAction.EXPENSE_ADDED, user, case, ("expense", expense.id))
    await session.commit()
    return await finance_out(session, case, user)


@router.patch("/expenses/{expense_id}")
async def update_expense(
    expense_id: uuid.UUID, body: ExpenseUpdate, user: Member, session: TenantSession
) -> FinanceOut:
    expense = await _get(session, CaseExpense, expense_id)
    case = await _get(session, VisaCase, expense.case_id)
    changes = body.model_dump(exclude_unset=True)
    if "description" in changes and changes["description"] is None:
        del changes["description"]
    changed = await _update_money(session, user, expense, changes, "spent_on")
    if changed:
        _audit(session, AuditAction.EXPENSE_UPDATED, user, case, ("expense", expense.id), changed)
    await session.commit()
    return await finance_out(session, case, user)


@router.delete("/expenses/{expense_id}")
async def delete_expense(expense_id: uuid.UUID, user: Member, session: TenantSession) -> FinanceOut:
    expense = await _get(session, CaseExpense, expense_id)
    case = await _get(session, VisaCase, expense.case_id)
    _audit(session, AuditAction.EXPENSE_DELETED, user, case, ("expense", expense.id))
    await session.delete(expense)
    await session.commit()
    return await finance_out(session, case, user)

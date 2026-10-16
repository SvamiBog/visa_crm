"""Settlements with registrars (backlog 1.10): each registrar's ledger in their currency —
the specialist's payments and the talons of the cases they put in a queue — and the
balance: an advance not spent yet or a debt (rules: app/core/registrar_ledger.py).

A talon is the case's own (case finances, 1.9), so the profit of a case and the ledger
count the same amount. Cases of a registrar with no talon yet are listed to charge one
from the price list. Closed by the «clients», «cases» and «finances» modules; events
carry ids, never amounts.
"""

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.api.finances import (
    _get,
    _rate,
    _tenant,
    first_queued_on,
    registrar_price_for,
)
from app.core.audit import record
from app.core.finance import to_base
from app.core.money import Amount
from app.core.registrar_ledger import Movement, build, is_low
from app.models import (
    AuditAction,
    CaseFinance,
    CaseStage,
    Contact,
    Registrar,
    RegistrarKind,
    RegistrarPayment,
    RegistrarPaymentMethod,
    VisaCase,
)

router = APIRouter(
    tags=["registrars"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("cases")),
        Depends(require_module("finances")),
    ],
)

Member = Annotated[CurrentUser, Depends(require_module("finances"))]
Talons = Annotated[int, Field(gt=0, le=1000)]
Note = Annotated[str, Field(max_length=200)]


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


class PaymentIn(BaseModel):
    """A payment in the registrar's currency: for `talons` talons, or an advance (none)."""

    paid_on: date
    amount: Amount
    method: RegistrarPaymentMethod
    talons: Talons | None = None
    note: Note | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class PaymentUpdate(BaseModel):
    paid_on: date | None = None
    amount: Amount | None = None
    method: RegistrarPaymentMethod | None = None
    talons: Talons | None = None
    note: Note | None = None

    _note = field_validator("note", mode="before")(_blank_is_none)


class TalonIn(BaseModel):
    case_id: uuid.UUID


class LineOut(BaseModel):
    kind: Literal["payment", "talon"]
    on: date
    amount: Decimal
    balance: Decimal  # after this line
    # A payment:
    payment_id: uuid.UUID | None = None
    method: RegistrarPaymentMethod | None = None
    talons: int | None = None
    note: str | None = None
    # A talon:
    case_id: uuid.UUID | None = None
    case_number: int | None = None
    contact_id: uuid.UUID | None = None
    contact_name: str | None = None
    destination_id: uuid.UUID | None = None


class CaseRef(BaseModel):
    case_id: uuid.UUID
    case_number: int
    contact_id: uuid.UUID
    contact_name: str
    destination_id: uuid.UUID


class MissingTalon(CaseRef):
    """A case of the registrar with no talon in its finances."""

    stage: CaseStage
    queued_on: date | None
    # The registrar's price for it; None — not in the price list.
    price: Decimal | None
    price_currency: str | None


class OtherCurrencyTalon(CaseRef):
    """A talon in another currency than the registrar's: not in the balance."""

    on: date
    amount: Decimal
    currency: str


class SummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: RegistrarKind
    is_active: bool
    currency: str
    low_balance: Decimal | None
    paid: Decimal
    charged: Decimal
    # Paid − charged: positive — an advance, negative — a debt.
    balance: Decimal
    talons: int
    low: bool
    missing: int
    last_payment_on: date | None


class LedgerOut(SummaryOut):
    lines: list[LineOut]  # newest first
    missing_talons: list[MissingTalon]
    other_currency: list[OtherCurrencyTalon]


# --- Reading -------------------------------------------------------------------------


def _ref(case: VisaCase, contact: Contact) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "case_number": case.number,
        "contact_id": contact.id,
        "contact_name": contact.full_name,
        "destination_id": case.destination_id,
    }


async def _talons(
    session: AsyncSession, registrar_id: uuid.UUID | None = None
) -> list[tuple[CaseFinance, VisaCase, Contact]]:
    query = (
        select(CaseFinance, VisaCase, Contact)
        .join(VisaCase, VisaCase.id == CaseFinance.case_id)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .where(VisaCase.registrar_id.is_not(None), CaseFinance.registrar_fee_amount.is_not(None))
    )
    if registrar_id is not None:
        query = query.where(VisaCase.registrar_id == registrar_id)
    return [(f, c, p) for f, c, p in (await session.execute(query)).all()]


async def _without_talon(
    session: AsyncSession, registrar_id: uuid.UUID | None = None
) -> list[tuple[VisaCase, Contact]]:
    query = (
        select(VisaCase, Contact)
        .join(Contact, Contact.id == VisaCase.contact_id)
        .outerjoin(CaseFinance, CaseFinance.case_id == VisaCase.id)
        .where(VisaCase.registrar_id.is_not(None), CaseFinance.registrar_fee_amount.is_(None))
        .order_by(VisaCase.number)
    )
    if registrar_id is not None:
        query = query.where(VisaCase.registrar_id == registrar_id)
    return [(c, p) for c, p in (await session.execute(query)).all()]


def _summary(
    registrar: Registrar,
    payments: list[RegistrarPayment],
    talons: list[tuple[CaseFinance, VisaCase, Contact]],
    missing: int,
) -> tuple[SummaryOut, Any]:
    movements = [Movement("payment", p.paid_on, p.amount, p.id, p.created_at) for p in payments]
    movements += [
        Movement("talon", f.registrar_fee_on, f.registrar_fee_amount, c.id, c.created_at)
        for f, c, _ in talons
        if f.registrar_fee_currency == registrar.currency
    ]
    ledger = build(movements)
    summary = SummaryOut(
        id=registrar.id,
        name=registrar.name,
        kind=registrar.kind,
        is_active=registrar.is_active,
        currency=registrar.currency,
        low_balance=registrar.low_balance,
        paid=ledger.paid,
        charged=ledger.charged,
        balance=ledger.balance,
        talons=ledger.talons,
        low=is_low(ledger.balance, registrar.low_balance),
        missing=missing,
        last_payment_on=max((p.paid_on for p in payments), default=None),
    )
    return summary, ledger


async def ledger_out(session: AsyncSession, registrar: Registrar) -> LedgerOut:
    payments = list(
        await session.scalars(
            select(RegistrarPayment).where(RegistrarPayment.registrar_id == registrar.id)
        )
    )
    talons = await _talons(session, registrar.id)
    without = await _without_talon(session, registrar.id)
    summary, ledger = _summary(registrar, payments, talons, len(without))

    by_payment = {p.id: p for p in payments}
    by_case = {c.id: (c, p) for _, c, p in talons}
    lines = []
    for line in reversed(ledger.lines):
        m = line.movement
        out = LineOut(kind=m.kind, on=m.on, amount=m.amount, balance=line.balance)
        if m.kind == "payment":
            p = by_payment[m.ref]
            out.payment_id, out.method, out.talons, out.note = p.id, p.method, p.talons, p.note
        else:
            for key, value in _ref(*by_case[m.ref]).items():
                setattr(out, key, value)
        lines.append(out)

    missing = []
    for case, contact in without:
        price = await registrar_price_for(session, case)
        missing.append(
            MissingTalon(
                **_ref(case, contact),
                stage=case.stage,
                queued_on=await first_queued_on(session, case.id),
                price=price.amount if price else None,
                price_currency=price.currency if price else None,
            )
        )
    # Queued clients first, by the day they were queued.
    missing.sort(key=lambda m: (m.queued_on is None, m.queued_on or date.max, m.case_number))

    other = [
        OtherCurrencyTalon(
            **_ref(c, p),
            on=f.registrar_fee_on,
            amount=f.registrar_fee_amount,
            currency=f.registrar_fee_currency,
        )
        for f, c, p in talons
        if f.registrar_fee_currency != registrar.currency
    ]
    return LedgerOut(
        **summary.model_dump(), lines=lines, missing_talons=missing, other_currency=other
    )


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    target: tuple[str, uuid.UUID],
    details: dict[str, Any],
) -> None:
    """Ids and field names only: no amounts."""
    record(
        session,
        action,
        actor=user,
        target_type=target[0],
        target_id=target[1],
        details=details,
    )


# --- Endpoints -----------------------------------------------------------------------


@router.get("/registrars/ledgers")
async def list_ledgers(user: Member, session: TenantSession) -> list[SummaryOut]:
    """Every registrar's balance, in their own currency."""
    registrars = list(await session.scalars(select(Registrar).order_by(Registrar.name)))
    payments: dict[uuid.UUID, list[RegistrarPayment]] = defaultdict(list)
    for p in await session.scalars(select(RegistrarPayment)):
        payments[p.registrar_id].append(p)
    talons: dict[uuid.UUID, list] = defaultdict(list)
    for row in await _talons(session):
        talons[row[1].registrar_id].append(row)
    missing: dict[uuid.UUID, int] = defaultdict(int)
    for case, _ in await _without_talon(session):
        missing[case.registrar_id] += 1
    return [
        _summary(r, payments[r.id], talons[r.id], missing[r.id])[0]
        for r in registrars
        if r.is_active or payments[r.id] or talons[r.id]
    ]


@router.get("/registrars/{registrar_id}/ledger")
async def get_ledger(registrar_id: uuid.UUID, user: Member, session: TenantSession) -> LedgerOut:
    return await ledger_out(session, await _get(session, Registrar, registrar_id))


@router.post("/registrars/{registrar_id}/payments", status_code=status.HTTP_201_CREATED)
async def add_payment(
    registrar_id: uuid.UUID, body: PaymentIn, user: Member, session: TenantSession
) -> LedgerOut:
    registrar = await _get(session, Registrar, registrar_id)
    payment = RegistrarPayment(
        tenant_id=registrar.tenant_id,
        registrar_id=registrar.id,
        created_by=user.id,
        **body.model_dump(),
    )
    session.add(payment)
    await session.flush()
    _audit(
        session,
        AuditAction.REGISTRAR_PAYMENT_ADDED,
        user,
        ("registrar_payment", payment.id),
        {"registrar": str(registrar.id)},
    )
    await session.commit()
    return await ledger_out(session, registrar)


@router.patch("/registrar-payments/{payment_id}")
async def update_payment(
    payment_id: uuid.UUID, body: PaymentUpdate, user: Member, session: TenantSession
) -> LedgerOut:
    payment = await _get(session, RegistrarPayment, payment_id)
    registrar = await _get(session, Registrar, payment.registrar_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("paid_on", "amount", "method"):
        if field in changes and changes[field] is None:
            del changes[field]  # required: null means «leave as is»
    changed = [f for f, v in changes.items() if getattr(payment, f) != v]
    for field, value in changes.items():
        setattr(payment, field, value)
    if changed:
        _audit(
            session,
            AuditAction.REGISTRAR_PAYMENT_UPDATED,
            user,
            ("registrar_payment", payment.id),
            {"registrar": str(registrar.id), "fields": ",".join(sorted(changed))},
        )
    await session.commit()
    return await ledger_out(session, registrar)


@router.delete("/registrar-payments/{payment_id}")
async def delete_payment(payment_id: uuid.UUID, user: Member, session: TenantSession) -> LedgerOut:
    payment = await _get(session, RegistrarPayment, payment_id)
    registrar = await _get(session, Registrar, payment.registrar_id)
    _audit(
        session,
        AuditAction.REGISTRAR_PAYMENT_DELETED,
        user,
        ("registrar_payment", payment.id),
        {"registrar": str(registrar.id)},
    )
    await session.delete(payment)
    await session.commit()
    return await ledger_out(session, registrar)


@router.post("/registrars/{registrar_id}/talons", status_code=status.HTTP_201_CREATED)
async def charge_talon(
    registrar_id: uuid.UUID, body: TalonIn, user: Member, session: TenantSession
) -> LedgerOut:
    """Charge the talon of a case from the registrar's price list: the day the client was
    queued (else today), at the bank's rate of that day for the case's finances."""
    registrar = await _get(session, Registrar, registrar_id)
    case = await _get(session, VisaCase, body.case_id)
    if case.registrar_id != registrar.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "registrar_mismatch")
    finance = await session.get(CaseFinance, case.id)
    if finance is not None and finance.registrar_fee_amount is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "talon_exists")
    price = await registrar_price_for(session, case)
    if price is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_registrar_price")
    tenant = await _tenant(session, user)
    day = await first_queued_on(session, case.id) or await workspace_today(session, tenant.id)
    rate = await _rate(price.currency, tenant.base_currency, day, None)
    if finance is None:
        finance = CaseFinance(case_id=case.id, tenant_id=case.tenant_id)
        session.add(finance)
    finance.registrar_fee_amount = price.amount
    finance.registrar_fee_currency = price.currency
    finance.registrar_fee_rate = rate
    finance.registrar_fee_base = to_base(price.amount, rate)
    finance.registrar_fee_on = day
    _audit(
        session,
        AuditAction.TALON_CHARGED,
        user,
        ("case", case.id),
        {"case": str(case.id), "contact": str(case.contact_id), "registrar": str(registrar.id)},
    )
    await session.commit()
    return await ledger_out(session, registrar)

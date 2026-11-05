"""The specialists' subscription to the CRM (roadmap 3.8, decisions.md → «Слоты и SaaS»).

The admin keeps the tariffs, gives a specialist a tariff, marks the payments received
outside the system and sees whose payment is overdue or coming up. Each change of a
workspace's subscription is logged under that workspace. The specialist sees their own
subscription, read-only; their employees do not see it.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    require_admin,
    require_specialist,
)
from app.core.audit import record
from app.core.clock import today_in
from app.core.money import Amount, Currency
from app.core.subscription import Status, Summary, amount_due, summarize
from app.models import AuditAction, SubscriptionPayment, Tariff, Tenant, User, UserRole

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
router = APIRouter(tags=["workspace"])

Admin = Annotated[CurrentUser, Depends(require_admin)]
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

TariffName = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]


class TariffIn(BaseModel):
    name: TariffName
    price: Amount
    currency: Currency


class TariffUpdate(BaseModel):
    """Only the fields sent are changed."""

    name: TariffName | None = None
    price: Amount | None = None
    currency: Currency | None = None
    is_active: bool | None = None


class TariffBrief(BaseModel):
    id: uuid.UUID
    name: str
    price: Decimal
    currency: str
    is_active: bool


class TariffOut(TariffBrief):
    # How many specialists are on the tariff.
    specialists: int


class SubscriptionIn(BaseModel):
    """The whole subscription. No tariff ends it; without a start date it keeps the
    current start, or starts today."""

    tariff_id: uuid.UUID | None
    starts_on: date | None = None
    trial_until: date | None = None


class PaymentIn(BaseModel):
    paid_on: date
    months: int = Field(default=1, ge=1, le=24)
    # Left out: the tariff's price for the months, in the tariff's currency.
    amount: Amount | None = None
    currency: Currency | None = None
    note: str | None = Field(default=None, max_length=200)


class PaymentOut(BaseModel):
    id: uuid.UUID
    paid_on: date
    months: int
    amount: Decimal
    currency: str
    note: str | None


class SubscriptionState(BaseModel):
    status: Status
    next_due_on: date | None
    overdue_days: int


class SubscriptionOut(SubscriptionState):
    tariff: TariffBrief | None
    starts_on: date | None
    trial_until: date | None
    # The newest first.
    payments: list[PaymentOut]


class SubscriptionRow(SubscriptionState):
    """A specialist in the admin's list of subscriptions."""

    tenant_id: uuid.UUID
    name: str
    email: str
    is_active: bool
    tariff: str | None
    last_paid_on: date | None


def _summary(tenant: Tenant, months_paid: int) -> Summary:
    # A tenant has a start date exactly when it has a tariff (a check in the database).
    return summarize(
        starts_on=tenant.subscription_starts_on,
        trial_until=tenant.trial_until,
        months_paid=months_paid,
        today=today_in(tenant.timezone),
    )


def _state(summary: Summary) -> dict[str, object]:
    return {
        "status": summary.status,
        "next_due_on": summary.next_due_on,
        "overdue_days": summary.overdue_days,
    }


async def subscription_state(session: AsyncSession, tenant: Tenant) -> SubscriptionState:
    """The state alone: the cabinet shows a notice when the payment is overdue."""
    months = await session.scalar(
        select(func.coalesce(func.sum(SubscriptionPayment.months), 0)).where(
            SubscriptionPayment.tenant_id == tenant.id
        )
    )
    return SubscriptionState.model_validate(_state(_summary(tenant, months or 0)))


async def _subscription(session: AsyncSession, tenant: Tenant) -> SubscriptionOut:
    tariff = await session.get(Tariff, tenant.tariff_id) if tenant.tariff_id else None
    payments = (
        await session.scalars(
            select(SubscriptionPayment)
            .where(SubscriptionPayment.tenant_id == tenant.id)
            .order_by(SubscriptionPayment.paid_on.desc(), SubscriptionPayment.created_at.desc())
        )
    ).all()
    return SubscriptionOut(
        **_state(_summary(tenant, sum(p.months for p in payments))),
        tariff=TariffBrief.model_validate(tariff, from_attributes=True) if tariff else None,
        starts_on=tenant.subscription_starts_on,
        trial_until=tenant.trial_until,
        payments=[PaymentOut.model_validate(p, from_attributes=True) for p in payments],
    )


async def _tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return tenant


def _record_on_tenant(
    session: AsyncSession,
    action: AuditAction,
    admin: CurrentUser,
    tenant_id: uuid.UUID,
    details: dict[str, object],
) -> None:
    """Logged under the workspace: the admin finds it in that workspace's log."""
    record(
        session,
        action,
        actor=admin,
        tenant_id=tenant_id,
        target_type="tenant",
        target_id=tenant_id,
        details=details,
    )


# --- Tariffs ---------------------------------------------------------------------------


async def _tariff_out(session: AsyncSession, tariff: Tariff) -> TariffOut:
    on_it = await session.scalar(select(func.count()).where(Tenant.tariff_id == tariff.id))
    return TariffOut(
        **TariffBrief.model_validate(tariff, from_attributes=True).model_dump(),
        specialists=on_it or 0,
    )


async def _name_taken(session: AsyncSession, name: str, but: uuid.UUID | None = None) -> bool:
    query = select(Tariff.id).where(func.lower(Tariff.name) == name.lower())
    if but is not None:
        query = query.where(Tariff.id != but)
    return await session.scalar(query) is not None


@admin_router.get("/tariffs")
async def list_tariffs(session: SystemSession) -> list[TariffOut]:
    """Active tariffs first."""
    tariffs = await session.scalars(select(Tariff).order_by(Tariff.is_active.desc(), Tariff.name))
    return [await _tariff_out(session, t) for t in tariffs.all()]


@admin_router.post("/tariffs", status_code=status.HTTP_201_CREATED)
async def create_tariff(body: TariffIn, admin: Admin, session: SystemSession) -> TariffOut:
    name = body.name.strip()
    if await _name_taken(session, name):
        raise HTTPException(status.HTTP_409_CONFLICT, "tariff_name_taken")
    tariff = Tariff(id=uuid.uuid4(), name=name, price=body.price, currency=body.currency)
    session.add(tariff)
    try:
        await session.flush()
    except IntegrityError:
        # The same name sent twice at once (a double click): the unique key keeps one.
        raise HTTPException(status.HTTP_409_CONFLICT, "tariff_name_taken") from None
    record(
        session,
        AuditAction.TARIFF_CREATED,
        actor=admin,
        tenant_id=None,
        target_type="tariff",
        target_id=tariff.id,
        details={"price": str(body.price), "currency": body.currency},
    )
    await session.commit()
    await session.refresh(tariff)
    return await _tariff_out(session, tariff)


@admin_router.patch("/tariffs/{tariff_id}")
async def update_tariff(
    tariff_id: uuid.UUID, body: TariffUpdate, admin: Admin, session: SystemSession
) -> TariffOut:
    """A new price applies to everyone on the tariff from their next payment."""
    tariff = await session.get(Tariff, tariff_id)
    if tariff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if await _name_taken(session, changes["name"], but=tariff.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "tariff_name_taken")
    changed = sorted(f for f, v in changes.items() if getattr(tariff, f) != v)
    for field, value in changes.items():
        setattr(tariff, field, value)
    if changed:
        record(
            session,
            AuditAction.TARIFF_UPDATED,
            actor=admin,
            tenant_id=None,
            target_type="tariff",
            target_id=tariff.id,
            details={"changed": changed},
        )
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "tariff_name_taken") from None
    return await _tariff_out(session, tariff)


# --- Subscriptions of the specialists --------------------------------------------------


async def _rows(session: AsyncSession) -> list[SubscriptionRow]:
    paid = dict(
        (tenant_id, (months, last))
        for tenant_id, months, last in (
            await session.execute(
                select(
                    SubscriptionPayment.tenant_id,
                    func.sum(SubscriptionPayment.months),
                    func.max(SubscriptionPayment.paid_on),
                ).group_by(SubscriptionPayment.tenant_id)
            )
        ).tuples()
    )
    rows = await session.execute(
        select(Tenant, User.email, Tariff.name)
        .join(User, (User.tenant_id == Tenant.id) & (User.role == UserRole.SPECIALIST))
        .outerjoin(Tariff, Tariff.id == Tenant.tariff_id)
        .order_by(Tenant.name)
    )
    out = []
    for tenant, email, tariff in rows.tuples():
        months, last = paid.get(tenant.id, (0, None))
        out.append(
            SubscriptionRow(
                **_state(_summary(tenant, months)),
                tenant_id=tenant.id,
                name=tenant.name,
                email=email,
                is_active=tenant.is_active,
                tariff=tariff,
                last_paid_on=last,
            )
        )
    return out


@admin_router.get("/subscriptions")
async def list_subscriptions(session: SystemSession) -> list[SubscriptionRow]:
    """Every specialist, with or without a tariff."""
    return await _rows(session)


@admin_router.get("/subscriptions/due")
async def due_subscriptions(session: SystemSession) -> list[SubscriptionRow]:
    """For the admin's home: overdue and coming-up payments of the specialists who are not
    blocked, the earliest date first."""
    due = [
        row
        for row in await _rows(session)
        if row.is_active and row.status in (Status.OVERDUE, Status.DUE_SOON)
    ]
    return sorted(due, key=lambda row: row.next_due_on or date.max)


@admin_router.get("/specialists/{tenant_id}/subscription")
async def get_subscription(tenant_id: uuid.UUID, session: SystemSession) -> SubscriptionOut:
    return await _subscription(session, await _tenant(session, tenant_id))


@admin_router.put("/specialists/{tenant_id}/subscription")
async def set_subscription(
    tenant_id: uuid.UUID, body: SubscriptionIn, admin: Admin, session: SystemSession
) -> SubscriptionOut:
    tenant = await _tenant(session, tenant_id)
    before = (tenant.tariff_id, tenant.subscription_starts_on, tenant.trial_until)
    if body.tariff_id is None:
        after: tuple[uuid.UUID | None, date | None, date | None] = (None, None, None)
    else:
        tariff = await session.get(Tariff, body.tariff_id)
        if tariff is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_tariff")
        # An inactive tariff stays with those already on it, but is not given anew.
        if not tariff.is_active and tariff.id != tenant.tariff_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "tariff_inactive")
        starts_on = body.starts_on or tenant.subscription_starts_on or today_in(tenant.timezone)
        if body.trial_until is not None and body.trial_until <= starts_on:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "trial_before_start")
        after = (tariff.id, starts_on, body.trial_until)
    tenant.tariff_id, tenant.subscription_starts_on, tenant.trial_until = after
    fields = ("tariff", "starts_on", "trial_until")
    changed = [f for f, old, new in zip(fields, before, after, strict=True) if old != new]
    if changed:
        _record_on_tenant(
            session,
            AuditAction.SUBSCRIPTION_UPDATED,
            admin,
            tenant.id,
            {"changed": changed, "tariff": str(after[0]) if after[0] else None},
        )
    await session.commit()
    return await _subscription(session, tenant)


@admin_router.post(
    "/specialists/{tenant_id}/subscription/payments", status_code=status.HTTP_201_CREATED
)
async def add_payment(
    tenant_id: uuid.UUID, body: PaymentIn, admin: Admin, session: SystemSession
) -> SubscriptionOut:
    tenant = await _tenant(session, tenant_id)
    tariff = await session.get(Tariff, tenant.tariff_id) if tenant.tariff_id else None
    if tariff is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_subscription")
    payment = SubscriptionPayment(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        paid_on=body.paid_on,
        months=body.months,
        amount=body.amount or amount_due(tariff.price, body.months),
        currency=body.currency or tariff.currency,
        note=(body.note or "").strip() or None,
    )
    session.add(payment)
    await session.flush()
    _record_on_tenant(
        session,
        AuditAction.SUBSCRIPTION_PAYMENT_RECORDED,
        admin,
        tenant.id,
        {
            "payment": str(payment.id),
            "months": payment.months,
            "amount": str(payment.amount),
            "currency": payment.currency,
        },
    )
    await session.commit()
    return await _subscription(session, tenant)


@admin_router.delete("/subscription-payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment(payment_id: uuid.UUID, admin: Admin, session: SystemSession) -> None:
    """A payment marked by mistake; the log keeps what it was."""
    payment = await session.get(SubscriptionPayment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    _record_on_tenant(
        session,
        AuditAction.SUBSCRIPTION_PAYMENT_DELETED,
        admin,
        payment.tenant_id,
        {
            "payment": str(payment.id),
            "paid_on": payment.paid_on.isoformat(),
            "months": payment.months,
            "amount": str(payment.amount),
            "currency": payment.currency,
        },
    )
    await session.delete(payment)
    await session.commit()


# --- The specialist's own --------------------------------------------------------------


@router.get("/subscription")
async def own_subscription(user: Specialist, session: TenantSession) -> SubscriptionOut:
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None  # the session dependency already checked the tenant is active
    return await _subscription(session, tenant)

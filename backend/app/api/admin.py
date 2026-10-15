import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, SystemSession, require_admin
from app.core.audit import record
from app.core.dictionaries import add_default_dictionaries
from app.core.modules import DEFAULT_MODULES, ModuleList, TaxRate
from app.core.money import Currency
from app.core.security import generate_temporary_password, hash_password
from app.core.two_factor import reset_two_factor
from app.models import AuditAction, RecoveryCode, Tenant, User, UserRole, UserSession

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

Admin = Annotated[CurrentUser, Depends(require_admin)]


class SpecialistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    base_currency: Currency = "BYN"
    tax_rate: TaxRate = Decimal("0")
    modules: ModuleList = list(DEFAULT_MODULES)


class SpecialistUpdate(BaseModel):
    """Only the fields sent are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_currency: Currency | None = None
    tax_rate: TaxRate | None = None
    modules: ModuleList | None = None


class SpecialistOut(BaseModel):
    tenant_id: uuid.UUID
    name: str
    email: str
    base_currency: str
    tax_rate: Decimal
    modules: list[str]
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class SpecialistCreated(BaseModel):
    specialist: SpecialistOut
    temporary_password: str


class TemporaryPassword(BaseModel):
    temporary_password: str


def _out(tenant: Tenant, owner: User) -> SpecialistOut:
    return SpecialistOut(
        tenant_id=tenant.id,
        name=tenant.name,
        email=owner.email,
        base_currency=tenant.base_currency,
        tax_rate=tenant.tax_rate,
        modules=tenant.modules,
        is_active=tenant.is_active,
        must_change_password=owner.must_change_password,
        last_login_at=owner.last_login_at,
        created_at=tenant.created_at,
    )


async def _get_specialist(session: SystemSession, tenant_id: uuid.UUID) -> tuple[Tenant, User]:
    row = (
        await session.execute(
            select(Tenant, User)
            .join(User, User.tenant_id == Tenant.id)
            .where(Tenant.id == tenant_id, User.role == UserRole.SPECIALIST)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return row.tuple()


def _record_on_tenant(
    session: SystemSession,
    action: AuditAction,
    admin: CurrentUser,
    tenant_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    """Admin actions on a workspace go into that workspace's log: the specialist sees them."""
    record(
        session,
        action,
        actor=admin,
        tenant_id=tenant_id,
        target_type="tenant",
        target_id=tenant_id,
        details=details,
    )


async def _sign_out_tenant(session: SystemSession, tenant_id: uuid.UUID) -> None:
    await session.execute(
        delete(UserSession).where(
            UserSession.user_id.in_(select(User.id).where(User.tenant_id == tenant_id))
        )
    )


@router.get("/specialists")
async def list_specialists(session: SystemSession) -> list[SpecialistOut]:
    rows = await session.execute(
        select(Tenant, User)
        .join(User, User.tenant_id == Tenant.id)
        .where(User.role == UserRole.SPECIALIST)
        .order_by(Tenant.created_at.desc())
    )
    return [_out(tenant, owner) for tenant, owner in rows.tuples()]


@router.post("/specialists", status_code=status.HTTP_201_CREATED)
async def create_specialist(
    body: SpecialistIn, admin: Admin, session: SystemSession
) -> SpecialistCreated:
    email = body.email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")

    password = generate_temporary_password()
    tenant = Tenant(
        id=uuid.uuid4(),
        name=body.name.strip(),
        base_currency=body.base_currency,
        tax_rate=body.tax_rate,
        modules=body.modules,
    )
    owner = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.SPECIALIST,
        must_change_password=True,
    )
    session.add(tenant)
    await session.flush()
    session.add(owner)
    add_default_dictionaries(session, tenant.id)
    record(
        session,
        AuditAction.SPECIALIST_CREATED,
        actor=admin,
        tenant_id=tenant.id,
        target_type="tenant",
        target_id=tenant.id,
        details={"name": tenant.name, "email": email},
    )
    await session.commit()
    await session.refresh(tenant)
    await session.refresh(owner)
    return SpecialistCreated(specialist=_out(tenant, owner), temporary_password=password)


@router.patch("/specialists/{tenant_id}")
async def update_specialist(
    tenant_id: uuid.UUID, body: SpecialistUpdate, admin: Admin, session: SystemSession
) -> SpecialistOut:
    tenant, owner = await _get_specialist(session, tenant_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    details: dict[str, Any] = {"changed": sorted(changes)}
    if "modules" in changes:
        details["modules_on"] = [m for m in body.modules or [] if m not in tenant.modules]
        details["modules_off"] = [m for m in tenant.modules if m not in (body.modules or [])]
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        setattr(tenant, field, value)
    if changes:
        _record_on_tenant(session, AuditAction.SPECIALIST_UPDATED, admin, tenant_id, details)
    await session.commit()
    return _out(tenant, owner)


@router.post("/specialists/{tenant_id}/block")
async def block_specialist(
    tenant_id: uuid.UUID, admin: Admin, session: SystemSession
) -> SpecialistOut:
    tenant, owner = await _get_specialist(session, tenant_id)
    tenant.is_active = False
    await _sign_out_tenant(session, tenant_id)
    _record_on_tenant(session, AuditAction.SPECIALIST_BLOCKED, admin, tenant_id)
    await session.commit()
    return _out(tenant, owner)


@router.post("/specialists/{tenant_id}/unblock")
async def unblock_specialist(
    tenant_id: uuid.UUID, admin: Admin, session: SystemSession
) -> SpecialistOut:
    tenant, owner = await _get_specialist(session, tenant_id)
    tenant.is_active = True
    _record_on_tenant(session, AuditAction.SPECIALIST_UNBLOCKED, admin, tenant_id)
    await session.commit()
    return _out(tenant, owner)


@router.post("/specialists/{tenant_id}/reset-password")
async def reset_specialist_password(
    tenant_id: uuid.UUID, admin: Admin, session: SystemSession
) -> TemporaryPassword:
    """New temporary password for the specialist: also lifts a lockout, turns off two-factor
    login (they set it up again, e.g. after losing the phone) and signs them out."""
    _, owner = await _get_specialist(session, tenant_id)
    password = generate_temporary_password()
    owner.password_hash = hash_password(password)
    owner.must_change_password = True
    owner.failed_login_count = 0
    owner.locked_until = None
    reset_two_factor(owner)
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == owner.id))
    await session.execute(delete(UserSession).where(UserSession.user_id == owner.id))
    _record_on_tenant(session, AuditAction.SPECIALIST_PASSWORD_RESET, admin, tenant_id)
    await session.commit()
    return TemporaryPassword(temporary_password=password)

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import SystemSession, require_admin
from app.core.security import generate_temporary_password, hash_password
from app.models import Tenant, User, UserRole, UserSession

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Currencies the specialist can keep accounts in (docs/decisions.md → «Деньги»).
Currency = Literal["BYN", "PLN", "USD", "EUR"]


class SpecialistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    base_currency: Currency = "BYN"


class SpecialistOut(BaseModel):
    tenant_id: uuid.UUID
    name: str
    email: str
    base_currency: str
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
async def create_specialist(body: SpecialistIn, session: SystemSession) -> SpecialistCreated:
    email = body.email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")

    password = generate_temporary_password()
    tenant = Tenant(id=uuid.uuid4(), name=body.name.strip(), base_currency=body.base_currency)
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
    await session.commit()
    await session.refresh(tenant)
    await session.refresh(owner)
    return SpecialistCreated(specialist=_out(tenant, owner), temporary_password=password)


@router.post("/specialists/{tenant_id}/block")
async def block_specialist(tenant_id: uuid.UUID, session: SystemSession) -> SpecialistOut:
    tenant, owner = await _get_specialist(session, tenant_id)
    tenant.is_active = False
    await _sign_out_tenant(session, tenant_id)
    await session.commit()
    return _out(tenant, owner)


@router.post("/specialists/{tenant_id}/unblock")
async def unblock_specialist(tenant_id: uuid.UUID, session: SystemSession) -> SpecialistOut:
    tenant, owner = await _get_specialist(session, tenant_id)
    tenant.is_active = True
    await session.commit()
    return _out(tenant, owner)


@router.post("/specialists/{tenant_id}/reset-password")
async def reset_specialist_password(
    tenant_id: uuid.UUID, session: SystemSession
) -> TemporaryPassword:
    """New temporary password for the specialist: also lifts a lockout and signs them out."""
    _, owner = await _get_specialist(session, tenant_id)
    password = generate_temporary_password()
    owner.password_hash = hash_password(password)
    owner.must_change_password = True
    owner.failed_login_count = 0
    owner.locked_until = None
    await session.execute(delete(UserSession).where(UserSession.user_id == owner.id))
    await session.commit()
    return TemporaryPassword(temporary_password=password)

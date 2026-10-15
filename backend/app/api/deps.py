import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker, scope_to_system, scope_to_tenant
from app.core.security import hash_session_token
from app.models import Tenant, User, UserRole, UserSession

SESSION_COOKIE = "visacrm_session"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    session_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: UserRole
    email: str
    must_change_password: bool


async def system_session() -> AsyncIterator[AsyncSession]:
    """Sees every tenant: only for login and the admin panel."""
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        yield session


async def current_user_any(request: Request) -> CurrentUser:
    """The logged-in user, even one who still has to replace a temporary password."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        row = (
            await session.execute(
                select(UserSession.id, User)
                .join(User, User.id == UserSession.user_id)
                .outerjoin(Tenant, Tenant.id == User.tenant_id)
                .where(
                    UserSession.token_hash == hash_session_token(token),
                    UserSession.expires_at > datetime.now(UTC),
                    User.is_active,
                    # Blocking a specialist's workspace signs out its employees too.
                    (User.tenant_id.is_(None)) | Tenant.is_active,
                )
            )
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    session_id, user = row
    return CurrentUser(
        id=user.id,
        session_id=session_id,
        tenant_id=user.tenant_id,
        role=user.role,
        email=user.email,
        must_change_password=user.must_change_password,
    )


async def current_user(user: Annotated[CurrentUser, Depends(current_user_any)]) -> CurrentUser:
    """The logged-in user who may use the app (temporary password already replaced)."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    return user


async def require_admin(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


async def require_tenant_user(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if user.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


async def tenant_session(
    user: Annotated[CurrentUser, Depends(require_tenant_user)],
) -> AsyncIterator[AsyncSession]:
    """Sees only the current user's tenant (Row-Level Security)."""
    assert user.tenant_id is not None
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, user.tenant_id)
        yield session


SystemSession = Annotated[AsyncSession, Depends(system_session)]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]

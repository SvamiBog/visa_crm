import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_in
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
    two_factor_enabled: bool
    ui_theme: str


async def system_session() -> AsyncIterator[AsyncSession]:
    """Sees every tenant: only for login and the admin panel."""
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        yield session


async def current_user_any(request: Request) -> CurrentUser:
    """The logged-in user, even one who still has to replace a temporary password.
    A login waiting for its second factor is not logged in."""
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
                    UserSession.mfa_pending.is_(False),
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
        two_factor_enabled=user.totp_enabled_at is not None,
        ui_theme=user.ui_theme,
    )


async def current_user_password_set(
    user: Annotated[CurrentUser, Depends(current_user_any)],
) -> CurrentUser:
    """Logged in with their own password (temporary one already replaced)."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    return user


async def current_user(
    user: Annotated[CurrentUser, Depends(current_user_password_set)],
) -> CurrentUser:
    """The logged-in user who may use the app: the temporary password is replaced.
    Two-factor login is optional for every role (docs/decisions.md)."""
    return user


async def require_admin(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


async def require_specialist(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    """The workspace owner (not an employee)."""
    if user.role != UserRole.SPECIALIST:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


async def require_tenant_user(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if user.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


async def tenant_session(
    user: Annotated[CurrentUser, Depends(require_tenant_user)],
) -> AsyncIterator[AsyncSession]:
    """Sees only the current user's tenant (Row-Level Security); an employee, only their
    cases and clients within it (EmployeeCase)."""
    assert user.tenant_id is not None
    employee_id = user.id if user.role == UserRole.EMPLOYEE else None
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, user.tenant_id, employee_id)
        yield session


SystemSession = Annotated[AsyncSession, Depends(system_session)]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


async def effective_modules(session: AsyncSession, user: CurrentUser) -> list[str]:
    """Modules the user may use: the workspace's; for an employee, only those also given
    to them (a module the admin turns off disappears for employees too)."""
    tenant_modules = await session.scalar(select(Tenant.modules).where(Tenant.id == user.tenant_id))
    if tenant_modules is None:
        return []
    if user.role != UserRole.EMPLOYEE:
        return list(tenant_modules)
    granted = await session.scalar(select(User.modules).where(User.id == user.id)) or []
    return [m for m in tenant_modules if m in granted]


def require_module(key: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Dependency for a module's endpoints: 403 module_disabled unless the user has it."""

    async def check(
        user: Annotated[CurrentUser, Depends(require_tenant_user)], session: TenantSession
    ) -> CurrentUser:
        if key not in await effective_modules(session, user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "module_disabled")
        return user

    return check


async def workspace_today(session: AsyncSession, tenant_id: uuid.UUID) -> date:
    """Today in the specialist's time zone: deadlines and waits are counted in it."""
    timezone = await session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    return today_in(timezone)

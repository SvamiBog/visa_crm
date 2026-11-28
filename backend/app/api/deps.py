"""Who is asking and what they may see: the dependencies every router is closed with
(CLAUDE.md → «Роли»).

The session cookie (backlog S18) gives the signed-in user; `current_user` lets them in
only once a temporary password is changed; `require_admin`, `require_specialist` and
`require_module` narrow it by role and by the modules granted. `TenantSession` is a
database session in the user's workspace (and, for an employee, their cases),
`SystemSession` — one for login and the admin panel (app/core/db.py).
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_in
from app.core.config import get_settings
from app.core.db import get_sessionmaker, scope_to_system, scope_to_tenant
from app.core.security import hash_session_token
from app.models import Tenant, User, UserRole, UserSession

# The session cookie (backlog S18, audit N10). On HTTPS its name carries the __Host- prefix:
# a browser takes a cookie of that name only from this very host, Secure, for Path=/ and
# without Domain, so a neighbouring site under the same parent domain (another
# *.up.railway.app, another subdomain of the site's own domain) cannot plant one. The prefix
# needs Secure, so a developer's machine on plain http keeps the plain name.
SESSION_COOKIE = "visacrm_session"
HOST_SESSION_COOKIE = f"__Host-{SESSION_COOKIE}"
# Browsers signed in on HTTPS before the prefix hold the plain name. Until this moment such a
# session still works there and moves to the new name with its next request, so the change
# signs nobody out; after it the plain name is ignored on HTTPS, and this transition can go.
# A session lasts a week (auth.SESSION_TTL): deployed by 14 December 2026, nobody signed in
# has to sign in again.
PLAIN_SESSION_COOKIE_UNTIL = datetime(2026, 12, 21, tzinfo=UTC)


def session_cookie_name() -> str:
    """The name the session cookie is set under."""
    return HOST_SESSION_COOKIE if get_settings().cookie_secure else SESSION_COOKIE


@dataclass(frozen=True)
class SessionCookie:
    token: str
    name: str  # the name it came under


def _cookie_values(request: Request, name: str) -> list[str]:
    """Every value the request carries under the name, on every Cookie line of it:
    request.cookies keeps only the last of several."""
    values = []
    for line in request.headers.getlist("cookie"):
        for pair in line.split(";"):
            key, _, value = pair.partition("=")
            if key.strip() == name:
                values.append(value.strip())
    return values


def read_session_cookie(request: Request) -> SessionCookie | None:
    """The session cookie the request comes with, or None.

    A name sent twice gives none: a neighbouring site can add a copy, and which copy is the
    user's own cannot be told (audit N10). On HTTPS the __Host- name decides whenever it
    comes; the plain one, which a neighbour can plant, counts only without it, and only
    until PLAIN_SESSION_COOKIE_UNTIL."""
    names = [session_cookie_name()]
    if names[0] == HOST_SESSION_COOKIE and datetime.now(UTC) < PLAIN_SESSION_COOKIE_UNTIL:
        names.append(SESSION_COOKIE)
    for name in names:
        values = _cookie_values(request, name)
        if values:
            return SessionCookie(values[0], name) if len(values) == 1 and values[0] else None
    return None


def set_session_cookie(response: Response, token: str, max_age: timedelta) -> None:
    """Give the browser its session cookie; on HTTPS a plain one from before the prefix goes."""
    secure = get_settings().cookie_secure
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=int(max_age.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    if secure:
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)


def delete_session_cookies(response: Response) -> None:
    """Clear the session cookie under both names. A browser takes the cookie that clears a
    __Host- one only when it is Secure too."""
    secure = get_settings().cookie_secure
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True)
    if secure:
        response.delete_cookie(HOST_SESSION_COOKIE, path="/", secure=True, httponly=True)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    session_id: uuid.UUID
    session_expires_at: datetime
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
    A login waiting for its second factor is not logged in.

    app/main.py calls it before a route runs, to size the request body; the routes take it
    through signed_in_user."""
    cookie = read_session_cookie(request)
    if cookie is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        row = (
            await session.execute(
                select(UserSession.id, UserSession.expires_at, User)
                .join(User, User.id == UserSession.user_id)
                .outerjoin(Tenant, Tenant.id == User.tenant_id)
                .where(
                    UserSession.token_hash == hash_session_token(cookie.token),
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
    session_id, expires_at, user = row
    return CurrentUser(
        id=user.id,
        session_id=session_id,
        session_expires_at=expires_at,
        tenant_id=user.tenant_id,
        role=user.role,
        email=user.email,
        must_change_password=user.must_change_password,
        two_factor_enabled=user.totp_enabled_at is not None,
        ui_theme=user.ui_theme,
    )


async def signed_in_user(request: Request, response: Response) -> CurrentUser:
    """current_user_any for the routes: a session that came under the plain name on HTTPS
    moves to the __Host- one for what is left of it."""
    user = await current_user_any(request)
    cookie = read_session_cookie(request)
    if cookie is not None and cookie.name != session_cookie_name():
        set_session_cookie(response, cookie.token, user.session_expires_at - datetime.now(UTC))
    return user


async def current_user_password_set(
    user: Annotated[CurrentUser, Depends(signed_in_user)],
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

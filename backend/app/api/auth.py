import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import SESSION_COOKIE, CurrentUser, SystemSession, current_user_any
from app.core.config import get_settings
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    hash_session_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.models import Tenant, User, UserRole, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_TTL = timedelta(days=7)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    must_change_password: bool
    tenant_id: uuid.UUID | None


def _me(user: User | CurrentUser) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
        tenant_id=user.tenant_id,
    )


@router.post("/login")
async def login(body: LoginIn, response: Response, session: SystemSession) -> MeOut:
    now = datetime.now(UTC)
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))

    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise HTTPException(status.HTTP_423_LOCKED, "account_locked")

    if user is None or not verify_password(user.password_hash, body.password):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + LOCKOUT
                user.failed_login_count = 0
            await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    tenant_active = user.tenant_id is None or await session.scalar(
        select(Tenant.is_active).where(Tenant.id == user.tenant_id)
    )
    if not user.is_active or not tenant_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_disabled")

    assert user.password_hash is not None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    await session.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at <= now)
    )
    token, token_hash = new_session_token()
    session.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=now + SESSION_TTL))
    await session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    return _me(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SystemSession) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await session.execute(
            delete(UserSession).where(UserSession.token_hash == hash_session_token(token))
        )
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user: Annotated[CurrentUser, Depends(current_user_any)]) -> MeOut:
    return _me(user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    current: Annotated[CurrentUser, Depends(current_user_any)],
    session: SystemSession,
) -> MeOut:
    user = await session.get_one(User, current.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_current_password")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password_unchanged")

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out every other device: a changed password must lock out whoever knew the old one.
    await session.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.id != current.session_id
        )
    )
    await session.commit()
    return _me(user)

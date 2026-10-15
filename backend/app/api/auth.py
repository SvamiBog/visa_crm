import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

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
from app.core.two_factor import (
    hash_recovery_code,
    looks_like_recovery_code,
    unseal_secret,
    verify_totp,
)
from app.models import TWO_FACTOR_REQUIRED_ROLES, RecoveryCode, Tenant, User, UserRole, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_TTL = timedelta(days=7)
# Time to enter the second-factor code after the password was accepted.
MFA_TTL = timedelta(minutes=10)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    must_change_password: bool
    two_factor_enabled: bool
    must_set_up_two_factor: bool
    tenant_id: uuid.UUID | None


class MfaRequiredOut(BaseModel):
    """Password accepted; send the authenticator code to /auth/login/verify."""

    mfa_required: Literal[True] = True


def _me(user: User | CurrentUser) -> MeOut:
    enabled = (
        user.two_factor_enabled
        if isinstance(user, CurrentUser)
        else user.totp_enabled_at is not None
    )
    return MeOut(
        id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
        two_factor_enabled=enabled,
        must_set_up_two_factor=user.role in TWO_FACTOR_REQUIRED_ROLES and not enabled,
        tenant_id=user.tenant_id,
    )


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def _register_failure(user: User, now: datetime) -> None:
    """Wrong passwords and wrong second-factor codes share one counter and one lockout."""
    user.failed_login_count += 1
    if user.failed_login_count >= MAX_FAILED_LOGINS:
        user.locked_until = now + LOCKOUT
        user.failed_login_count = 0


async def _can_sign_in(session: SystemSession, user: User) -> bool:
    tenant_active = user.tenant_id is None or await session.scalar(
        select(Tenant.is_active).where(Tenant.id == user.tenant_id)
    )
    return user.is_active and bool(tenant_active)


def _set_cookie(response: Response, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )


def _complete_login(user: User, now: datetime) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now


@router.post("/login")
async def login(
    body: LoginIn, response: Response, session: SystemSession
) -> MeOut | MfaRequiredOut:
    now = datetime.now(UTC)
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))

    if user is not None and _is_locked(user, now):
        raise HTTPException(status.HTTP_423_LOCKED, "account_locked")

    if user is None or not verify_password(user.password_hash, body.password):
        if user is not None:
            _register_failure(user, now)
            await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    if not await _can_sign_in(session, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_disabled")

    assert user.password_hash is not None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await session.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at <= now)
    )
    token, token_hash = new_session_token()
    mfa_pending = user.totp_enabled_at is not None
    ttl = MFA_TTL if mfa_pending else SESSION_TTL
    session.add(
        UserSession(
            user_id=user.id, token_hash=token_hash, expires_at=now + ttl, mfa_pending=mfa_pending
        )
    )
    if not mfa_pending:
        _complete_login(user, now)
    # With 2FA the failed-attempt counter is not reset yet: otherwise someone who knows
    # the password could guess codes forever by logging in again between guesses.
    await session.commit()
    _set_cookie(response, token, ttl)
    return MfaRequiredOut() if mfa_pending else _me(user)


async def _check_second_factor(session: SystemSession, user: User, code: str) -> bool:
    if looks_like_recovery_code(code):
        recovery = await session.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == hash_recovery_code(code),
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if recovery is None:
            return False
        recovery.used_at = datetime.now(UTC)
        return True

    assert user.totp_secret_encrypted is not None
    secret = unseal_secret(user.id, user.totp_secret_encrypted)
    step = verify_totp(secret, code, user.totp_last_step)
    if step is None:
        return False
    user.totp_last_step = step
    return True


@router.post("/login/verify")
async def verify_login(
    body: CodeIn, request: Request, response: Response, session: SystemSession
) -> MeOut:
    """Second step: the authenticator code or a recovery code."""
    now = datetime.now(UTC)
    token = request.cookies.get(SESSION_COOKIE)
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_session_token(token or ""),
                UserSession.mfa_pending.is_(True),
                UserSession.expires_at > now,
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_expired")
    pending, user = row.tuple()

    if _is_locked(user, now):
        raise HTTPException(status.HTTP_423_LOCKED, "account_locked")
    if not await _can_sign_in(session, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_disabled")

    if not await _check_second_factor(session, user, body.code):
        _register_failure(user, now)
        if _is_locked(user, now):
            await session.delete(pending)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_code")

    pending.mfa_pending = False
    pending.expires_at = now + SESSION_TTL
    _complete_login(user, now)
    await session.commit()
    assert token is not None
    _set_cookie(response, token, SESSION_TTL)
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

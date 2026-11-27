"""Login (backlog 0.4, 0.5; hardened in S9): email and password, the second-factor code
when two-factor login is on, logout, the signed-in user with their preferences, and the
change of password that a temporary password requires.

Sessions are server-side, in the system scope; the cookie is kept in app/api/deps.py. Ten
misses lock an account for 15 minutes and answer like an unknown address; every login,
miss and lockout is in the audit log, without the password or the code.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from app.api.deps import (
    CurrentUser,
    SystemSession,
    current_user,
    delete_session_cookies,
    read_session_cookie,
    set_session_cookie,
    signed_in_user,
)
from app.core.audit import client_ip, record
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    ClientFailures,
    check_password,
    hash_password,
    hash_session_token,
    make_password_hash,
    new_session_token,
    password_needs_rehash,
)
from app.core.two_factor import (
    looks_like_recovery_code,
    unseal_secret,
    use_recovery_code,
    verify_totp,
)
from app.models import (
    AuditAction,
    Tenant,
    UiTheme,
    User,
    UserRole,
    UserSession,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(days=7)
# Time to enter the second-factor code after the password was accepted.
MFA_TTL = timedelta(minutes=10)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)
# Failed logins one client (address) may make in a window, whichever accounts they name
# (audit N6, h01.7). Past them the client is paused before any password is checked. Far
# above what people at one office mistype, and well below what guessing needs.
MAX_CLIENT_FAILURES = 30
# The same 15 minutes the «too many attempts» text promises.
CLIENT_WINDOW = LOCKOUT


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class PreferencesIn(BaseModel):
    ui_theme: UiTheme


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    must_change_password: bool
    two_factor_enabled: bool
    tenant_id: uuid.UUID | None
    ui_theme: str


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
        tenant_id=user.tenant_id,
        ui_theme=user.ui_theme,
    )


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def _count_miss(user: User, now: datetime) -> bool:
    """One more miss on the counter that wrong passwords and codes share; the tenth locks
    the account. Whether this one did."""
    user.failed_login_count += 1
    if user.failed_login_count < MAX_FAILED_LOGINS:
        return False
    user.locked_until = now + LOCKOUT
    user.failed_login_count = 0
    return True


async def _record_miss(session: SystemSession, user: User, reason: str, locked: bool) -> None:
    """The miss in the audit log, in a savepoint of its own: an entry the log turns down
    is lost alone, not with the count that stops guessing (audit N2). The count is
    written first, outside the savepoint, so a failure of its own still ends the request."""
    await session.flush()
    try:
        async with session.begin_nested():
            record(session, AuditAction.LOGIN_FAILED, actor=user, details={"reason": reason})
            if locked:
                record(session, AuditAction.ACCOUNT_LOCKED, actor=user)
    except DBAPIError as error:
        # Ids and the kind of failure only: the database's message may quote the values.
        logger.error(
            "The audit log refused a failed login of user %s (%s)", user.id, type(error).__name__
        )


async def _register_failure(session: SystemSession, user: User, now: datetime, reason: str) -> None:
    """Wrong passwords and wrong second-factor codes share one counter and one lockout.
    The caller holds the user's row locked (FOR UPDATE): misses sent at once must not
    read the same count and lose each other."""
    await _record_miss(session, user, reason, _count_miss(user, now))


async def _sign_out_locked(session: SystemSession, user: User) -> NoReturn:
    """A locked account loses every session, the asking one too: whoever guesses the
    password through a stolen session has to go back to the login, which is locked."""
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    raise HTTPException(status.HTTP_423_LOCKED, "account_locked")


async def refuse_if_locked(session: SystemSession, user: User) -> None:
    """A form of the settings checks no password or code of a locked account: it ends the
    account's sessions instead. The caller holds the user's row locked."""
    if _is_locked(user, datetime.now(UTC)):
        await _sign_out_locked(session, user)


async def sign_out_other_devices(session: SystemSession, current: CurrentUser) -> None:
    """End every session of the user but the asking one, logins waiting for their code
    included: a change to how the account signs in must lock out whoever held a session
    from before."""
    await session.execute(
        delete(UserSession).where(
            UserSession.user_id == current.id, UserSession.id != current.session_id
        )
    )


async def reject_wrong_secret(
    session: SystemSession, user: User, reason: str, detail: str
) -> NoReturn:
    """A wrong password or code given in the settings counts with wrong logins; the miss
    that locks the account ends its sessions."""
    now = datetime.now(UTC)
    await _register_failure(session, user, now, reason)
    if _is_locked(user, now):
        await _sign_out_locked(session, user)
    await session.commit()
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)


async def check_current_password(
    session: SystemSession, user: User, password: str, reason: str
) -> None:
    """The current password asked again by a form of the settings, with the user's row
    locked (FOR UPDATE). A stolen session must not guess it without limit: misses count
    with wrong logins, and a locked account checks nothing more."""
    await refuse_if_locked(session, user)
    if not await check_password(user.password_hash, password):
        await reject_wrong_secret(session, user, reason, "invalid_current_password")


async def _can_sign_in(session: SystemSession, user: User) -> bool:
    tenant_active = user.tenant_id is None or await session.scalar(
        select(Tenant.is_active).where(Tenant.id == user.tenant_id)
    )
    return user.is_active and bool(tenant_active)


def _complete_login(user: User, now: datetime) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now


def _client_failures(request: Request) -> ClientFailures:
    """The failed logins of each client, kept with the app: a new app (each test makes
    one) starts from none."""
    failures = getattr(request.app.state, "client_failures", None)
    if failures is None:
        failures = ClientFailures(MAX_CLIENT_FAILURES, CLIENT_WINDOW.total_seconds())
        request.app.state.client_failures = failures
    return failures


async def _take_attempt(
    session: SystemSession, email: str, now: datetime
) -> tuple[User, datetime | None] | None:
    """The account and the lock this try set as the tenth miss, or None: no such account,
    or it is locked. A short transaction of its own, closed before the password is checked
    (audit N6: argon2 must not hold a connection).

    The try counts as a miss before its password is checked, under the row lock, so tries
    sent at once still take turns at the counter and a burst checks no more passwords
    than tries made one by one. A right password gives the miss back (_give_back); a try
    that never comes back (the process stopped) stays a miss."""
    user = await session.scalar(select(User).where(User.email == email).with_for_update())
    if user is None or _is_locked(user, now):
        await session.rollback()
        return None
    lock = user.locked_until if _count_miss(user, now) else None
    await session.commit()
    return user, lock


def _give_back(user: User, lock: datetime | None) -> None:
    """A right password is not a miss: undo what _take_attempt counted."""
    if lock is None:
        user.failed_login_count = max(user.failed_login_count - 1, 0)
    elif user.locked_until == lock:
        # The try was the tenth: its lock goes, and the nine misses before it still count.
        user.locked_until = None
        user.failed_login_count = MAX_FAILED_LOGINS - 1


@router.post("/login")
async def login(
    body: LoginIn, request: Request, response: Response, session: SystemSession
) -> MeOut | MfaRequiredOut:
    # Too many failures from this client: paused before anything is looked up or checked,
    # whatever the address and password, so the pause tells nothing about them. The
    # address is the one the audit log records (app/main.py).
    clients, client = _client_failures(request), client_ip.get() or ""
    mark = clients.take(client)
    if mark is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account_locked")

    taken = await _take_attempt(session, body.email.strip().lower(), datetime.now(UTC))
    user, lock = taken or (None, None)
    password_hash = user.password_hash if user else None
    # No transaction is open while the password is checked. An unknown or a locked address
    # is checked against a stand-in hash: the same answer after the same work as a wrong
    # password, so neither tells which addresses have an account (audit L4).
    password_ok = await check_password(password_hash, body.password)
    if user is None or not password_ok:
        if user is not None:
            await _record_miss(session, user, "password", locked=lock is not None)
            await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    clients.give_back(client, mark)
    assert password_hash is not None
    new_hash = (
        await make_password_hash(body.password) if password_needs_rehash(password_hash) else None
    )
    now = datetime.now(UTC)
    user = await session.get(User, user.id, with_for_update=True, populate_existing=True)
    if user is None:  # deleted while the password was checked
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    _give_back(user, lock)
    if _is_locked(user, now):
        # Misses that came in while the password was checked locked the account.
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    if not await _can_sign_in(session, user):
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_disabled")

    if new_hash is not None and user.password_hash == password_hash:
        user.password_hash = new_hash
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
        record(session, AuditAction.LOGIN, actor=user, details={"method": "password"})
    # With 2FA the failed-attempt counter is not reset yet: otherwise someone who knows
    # the password could guess codes forever by logging in again between guesses.
    await session.commit()
    set_session_cookie(response, token, ttl)
    return MfaRequiredOut() if mfa_pending else _me(user)


async def _check_second_factor(session: SystemSession, user: User, code: str) -> str | None:
    """The method that matched ("totp" or "recovery_code"), or None."""
    if looks_like_recovery_code(code):
        return "recovery_code" if await use_recovery_code(session, user.id, code) else None

    assert user.totp_secret_encrypted is not None
    secret = unseal_secret(user.id, user.totp_secret_encrypted)
    step = verify_totp(secret, code, user.totp_last_step)
    if step is None:
        return None
    user.totp_last_step = step
    return "totp"


@router.post("/login/verify")
async def verify_login(
    body: CodeIn, request: Request, response: Response, session: SystemSession
) -> MeOut:
    """Second step: the authenticator code or a recovery code."""
    now = datetime.now(UTC)
    cookie = read_session_cookie(request)
    token = cookie.token if cookie else ""
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_session_token(token),
                UserSession.mfa_pending.is_(True),
                UserSession.expires_at > now,
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login_expired")
    pending, user = row

    if _is_locked(user, now):
        raise HTTPException(status.HTTP_423_LOCKED, "account_locked")
    if not await _can_sign_in(session, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account_disabled")

    method = await _check_second_factor(session, user, body.code)
    if method is None:
        await _register_failure(session, user, now, "code")
        if _is_locked(user, now):
            await session.delete(pending)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_code")

    pending.mfa_pending = False
    pending.expires_at = now + SESSION_TTL
    _complete_login(user, now)
    record(session, AuditAction.LOGIN, actor=user, details={"method": method})
    await session.commit()
    set_session_cookie(response, token, SESSION_TTL)
    return _me(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SystemSession) -> None:
    cookie = read_session_cookie(request)
    if cookie:
        row = (
            await session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_hash == hash_session_token(cookie.token))
            )
        ).first()
        if row is not None:
            user_session, user = row
            await session.delete(user_session)
            if not user_session.mfa_pending:
                record(session, AuditAction.LOGOUT, actor=user)
            await session.commit()
    delete_session_cookies(response)


@router.get("/me")
async def me(user: Annotated[CurrentUser, Depends(signed_in_user)]) -> MeOut:
    return _me(user)


@router.patch("/preferences")
async def update_preferences(
    body: PreferencesIn,
    current: Annotated[CurrentUser, Depends(current_user)],
    session: SystemSession,
) -> MeOut:
    """The user's own interface settings; they follow the user to every device."""
    user = await session.get_one(User, current.id)
    user.ui_theme = body.ui_theme.value
    await session.commit()
    return _me(user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    current: Annotated[CurrentUser, Depends(signed_in_user)],
    session: SystemSession,
) -> MeOut:
    user = await session.get_one(User, current.id, with_for_update=True)
    await check_current_password(session, user, body.current_password, "password_change_password")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password_unchanged")

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # A changed password must lock out whoever knew the old one.
    await sign_out_other_devices(session, current)
    record(session, AuditAction.PASSWORD_CHANGED, actor=user)
    await session.commit()
    return _me(user)

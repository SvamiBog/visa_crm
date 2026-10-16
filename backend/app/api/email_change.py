"""Changing the login email from the account settings.

The user asks with their current password; a 6-digit code goes to the new address and the
email changes only once that code is entered here. Then the old address gets a notice,
and every other device is signed out, as after a password change. Audit events carry no
addresses.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.auth import MeOut, _me, _register_failure
from app.api.deps import CurrentUser, SystemSession, current_user
from app.core.audit import record
from app.core.mail import Mail, MailFailed, MailNotConfigured, send_mail
from app.core.security import MAX_PASSWORD_LENGTH, hash_password, verify_password
from app.models import AuditAction, EmailChange, User, UserSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/email-change", tags=["auth"])

CODE_TTL = timedelta(minutes=30)
RESEND_AFTER = timedelta(seconds=60)
MAX_CODE_ATTEMPTS = 5
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class EmailChangeIn(BaseModel):
    new_email: Annotated[str, Field(max_length=320, pattern=EMAIL)]
    current_password: Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)]

    @field_validator("new_email", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class CodeIn(BaseModel):
    code: Annotated[str, Field(pattern=r"^\d{6}$")]

    @field_validator("code", mode="before")
    @classmethod
    def digits_only(cls, value: object) -> object:
        return "".join(value.split()) if isinstance(value, str) else value


class PendingOut(BaseModel):
    new_email: str
    expires_at: datetime
    # A new code can be asked for from this moment.
    resend_at: datetime


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def mask(email: str) -> str:
    """an***@mail.by: enough to recognise the address in a notice."""
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}"


def _pending_out(pending: EmailChange) -> PendingOut:
    return PendingOut(
        new_email=pending.new_email,
        expires_at=pending.expires_at,
        resend_at=pending.sent_at + RESEND_AFTER,
    )


def code_letter(to: str, code: str) -> Mail:
    minutes = int(CODE_TTL.total_seconds() // 60)
    return Mail(
        to=to,
        subject=f"VisaCRM: код подтверждения почты {code}",
        text=(
            f"Код для смены почты в VisaCRM: {code}\n"
            f"Он действует {minutes} минут. Если вы не меняли почту, "
            "просто не отвечайте на это письмо.\n\n"
            f"Your VisaCRM email confirmation code: {code}\n"
            f"It is valid for {minutes} minutes. If you did not ask for it, ignore this letter.\n"
        ),
    )


def notice_letter(to: str, new_email: str) -> Mail:
    masked = mask(new_email)
    return Mail(
        to=to,
        subject="VisaCRM: почта для входа изменена",
        text=(
            f"Почта для входа в ваш аккаунт VisaCRM изменена на {masked}.\n"
            "Если это сделали не вы, сразу свяжитесь с администратором сервиса.\n\n"
            f"The login email of your VisaCRM account was changed to {masked}.\n"
            "If it was not you, contact the service administrator at once.\n"
        ),
    )


@router.get("")
async def pending_change(
    current: Annotated[CurrentUser, Depends(current_user)], session: SystemSession
) -> PendingOut | None:
    """The change waiting for its code, if any."""
    pending = await session.get(EmailChange, current.id)
    if pending is None or pending.expires_at <= datetime.now(UTC):
        return None
    return _pending_out(pending)


@router.post("")
async def request_change(
    body: EmailChangeIn,
    current: Annotated[CurrentUser, Depends(current_user)],
    session: SystemSession,
) -> PendingOut:
    """Check the password and send a code to the new address (again: a new code)."""
    user = await session.get_one(User, current.id)
    now = datetime.now(UTC)
    if not verify_password(user.password_hash, body.current_password):
        # Counted with wrong login passwords: a stolen session cannot guess the password.
        _register_failure(session, user, now, "email_change_password")
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_current_password")
    if body.new_email == user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email_unchanged")
    if await session.scalar(select(User.id).where(User.email == body.new_email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")

    pending = await session.get(EmailChange, user.id)
    if pending is not None and pending.sent_at + RESEND_AFTER > now:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "code_sent_recently")
    if pending is None:
        pending = EmailChange(user_id=user.id)
        session.add(pending)
    code = new_code()
    pending.new_email = body.new_email
    pending.code_hash = hash_password(code)
    pending.attempts = 0
    pending.sent_at = now
    pending.expires_at = now + CODE_TTL
    record(session, AuditAction.EMAIL_CHANGE_REQUESTED, actor=user)
    await session.flush()
    # Sent before the commit: a code that never left is not kept.
    try:
        await send_mail(code_letter(body.new_email, code))
    except MailNotConfigured as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "mail_unavailable") from error
    except MailFailed as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "mail_failed") from error
    await session.commit()
    return _pending_out(pending)


@router.post("/confirm")
async def confirm_change(
    body: CodeIn,
    current: Annotated[CurrentUser, Depends(current_user)],
    session: SystemSession,
) -> MeOut:
    user = await session.get_one(User, current.id)
    pending = await session.get(EmailChange, user.id)
    now = datetime.now(UTC)
    if pending is None or pending.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code_expired")
    if not verify_password(pending.code_hash, body.code):
        pending.attempts += 1
        if pending.attempts >= MAX_CODE_ATTEMPTS:
            # Too many guesses: start again, with the password.
            await session.delete(pending)
            await session.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "code_expired")
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_code")

    old_email, new_email = user.email, pending.new_email
    record(session, AuditAction.EMAIL_CHANGED, actor=user)
    user.email = new_email
    try:
        await session.flush()
    except IntegrityError as error:  # someone took the address after the code was sent
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken") from error
    await session.delete(pending)
    # Sign out every other device, as after a password change.
    await session.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.id != current.session_id
        )
    )
    await session.commit()

    try:
        await send_mail(notice_letter(old_email, new_email))
    except (MailNotConfigured, MailFailed) as error:
        # The change is done; a lost notice must not undo it.
        logger.warning("Email change notice not sent: %s", type(error).__name__)
    return _me(user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_change(
    current: Annotated[CurrentUser, Depends(current_user)], session: SystemSession
) -> None:
    """The row stays, expired: cancelling does not lift the pause before the next code."""
    pending = await session.get(EmailChange, current.id)
    if pending is not None:
        pending.expires_at = datetime.now(UTC)
        await session.commit()

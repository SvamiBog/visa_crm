from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete

from app.api.auth import CodeIn, _check_second_factor, _register_failure
from app.api.deps import CurrentUser, SystemSession, current_user_password_set
from app.core.audit import record
from app.core.security import MAX_PASSWORD_LENGTH, verify_password
from app.core.two_factor import (
    generate_recovery_codes,
    hash_recovery_code,
    new_secret,
    provisioning_uri,
    qr_code_data_uri,
    reset_two_factor,
    seal_secret,
    unseal_secret,
    verify_totp,
)
from app.models import AuditAction, RecoveryCode, User

router = APIRouter(prefix="/auth/two-factor", tags=["auth"])

CurrentUserPasswordSet = Annotated[CurrentUser, Depends(current_user_password_set)]


class SetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_code: str  # data: URI with an SVG image


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class DisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    # The authenticator code or a recovery code.
    code: str = Field(min_length=6, max_length=32)


@router.post("/setup")
async def start_setup(current: CurrentUserPasswordSet, session: SystemSession) -> SetupOut:
    """New TOTP secret for the authenticator app; active only after /enable."""
    user = await session.get_one(User, current.id)
    if user.totp_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_already_enabled")
    secret = new_secret()
    user.totp_secret_encrypted = seal_secret(user.id, secret)
    user.totp_last_step = None
    await session.commit()
    uri = provisioning_uri(secret, user.email)
    return SetupOut(secret=secret, otpauth_uri=uri, qr_code=qr_code_data_uri(uri))


@router.post("/enable")
async def enable(
    body: CodeIn, current: CurrentUserPasswordSet, session: SystemSession
) -> RecoveryCodesOut:
    """Confirm the app works with one code; returns recovery codes, shown only once."""
    user = await session.get_one(User, current.id)
    if user.totp_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_already_enabled")
    if user.totp_secret_encrypted is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "two_factor_setup_not_started")
    step = verify_totp(unseal_secret(user.id, user.totp_secret_encrypted), body.code, None)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_code")

    user.totp_enabled_at = datetime.now(UTC)
    user.totp_last_step = step
    codes = generate_recovery_codes()
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    session.add_all(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code(c)) for c in codes)
    record(session, AuditAction.TWO_FACTOR_ENABLED, actor=user)
    await session.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable(body: DisableIn, current: CurrentUserPasswordSet, session: SystemSession) -> None:
    """Turn two-factor login off (it is optional): the password and a current code, so a
    stolen session alone cannot do it. Wrong ones count towards the login lockout."""
    user = await session.get_one(User, current.id, with_for_update=True)
    if user.totp_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_not_enabled")
    now = datetime.now(UTC)
    if not verify_password(user.password_hash, body.password):
        _register_failure(session, user, now, "two_factor_disable_password")
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_current_password")
    if await _check_second_factor(session, user, body.code) is None:
        _register_failure(session, user, now, "two_factor_disable_code")
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_code")
    reset_two_factor(user)
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    record(session, AuditAction.TWO_FACTOR_DISABLED, actor=user)
    await session.commit()

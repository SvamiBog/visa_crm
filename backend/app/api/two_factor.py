from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete

from app.api.auth import CodeIn
from app.api.deps import CurrentUser, SystemSession, current_user_password_set
from app.core.two_factor import (
    generate_recovery_codes,
    hash_recovery_code,
    new_secret,
    provisioning_uri,
    qr_code_data_uri,
    seal_secret,
    unseal_secret,
    verify_totp,
)
from app.models import RecoveryCode, User

router = APIRouter(prefix="/auth/two-factor", tags=["auth"])

CurrentUserPasswordSet = Annotated[CurrentUser, Depends(current_user_password_set)]


class SetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_code: str  # data: URI with an SVG image


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


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
    await session.commit()
    return RecoveryCodesOut(recovery_codes=codes)

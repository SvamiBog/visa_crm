"""Two-factor login: TOTP (RFC 6238) and one-time recovery codes.

Recovery codes are kept as an HMAC with a key derived from SECRET_KEY (backlog S16):
a copy of the database alone gives nothing to guess them against.
"""

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pyotp
import segno
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import seal, unseal
from app.models import RecoveryCode

if TYPE_CHECKING:
    from app.models import User

ISSUER = "VisaCRM"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
# Codes from the previous and next 30-second step are accepted too: phone clocks drift.
_DRIFT_STEPS = 1
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# Marks a recovery code kept as HMAC; the old unsalted SHA-256 is 64 hex digits.
RECOVERY_HASH_PREFIX = "hmac:"
# The value in .env.example and the settings' default: known to everyone.
_DEFAULT_SECRET_KEYS = ("", "change-me")


def _now() -> float:
    return time.time()


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_code_data_uri(uri: str) -> str:
    """SVG QR code as a data: URI. Generated here so the secret never leaves our server."""
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


def totp_secret_aad(user_id: uuid.UUID) -> bytes:
    return b"totp:" + user_id.bytes


def seal_secret(user_id: uuid.UUID, secret: str) -> bytes:
    return seal(secret.encode(), totp_secret_aad(user_id))


def unseal_secret(user_id: uuid.UUID, blob: bytes) -> str:
    return unseal(blob, totp_secret_aad(user_id)).decode()


def verify_totp(secret: str, code: str, last_used_step: int | None) -> int | None:
    """The time step the code belongs to, or None if the code is wrong or already used.

    A code is accepted once: steps up to last_used_step are rejected (no replay).
    """
    code = code.replace(" ", "")
    # ASCII only: digits of other scripts pass isdigit() and are a wrong code (audit h02.7).
    if len(code) != 6 or not (code.isascii() and code.isdigit()):
        return None
    totp = pyotp.TOTP(secret)
    current = int(_now() // STEP_SECONDS)
    for step in range(current - _DRIFT_STEPS, current + _DRIFT_STEPS + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def reset_two_factor(user: "User") -> None:
    """Turn two-factor login off; the caller also deletes the user's recovery codes."""
    user.totp_secret_encrypted = None
    user.totp_enabled_at = None
    user.totp_last_step = None


def looks_like_recovery_code(code: str) -> bool:
    return not code.replace(" ", "").isdigit()


def generate_recovery_codes() -> list[str]:
    def one() -> str:
        chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        return f"{chars[:5]}-{chars[5:]}"

    return [one() for _ in range(RECOVERY_CODE_COUNT)]


class RecoveryKeyMissing(RuntimeError):
    """SECRET_KEY is empty or the default outside local development and tests."""


def _recovery_key() -> bytes:
    settings = get_settings()
    if settings.secret_key in _DEFAULT_SECRET_KEYS and settings.env not in ("local", "test"):
        raise RecoveryKeyMissing("SECRET_KEY is not set")
    # A key of its own: another use of SECRET_KEY never computes the same values.
    return hmac.new(settings.secret_key.encode(), b"visacrm:recovery-codes", "sha256").digest()


def _digest(code: str) -> bytes:
    normalized = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256(normalized.encode()).digest()


def _keyed(digest: bytes) -> str:
    """The stored form: an HMAC with the server key (audit h02.3). It is taken of the
    SHA-256 that used to be stored, so a stored old hash is converted without the code."""
    mac = hmac.new(_recovery_key(), digest, "sha256").digest()
    return RECOVERY_HASH_PREFIX + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def hash_recovery_code(code: str) -> str:
    return _keyed(_digest(code))


async def use_recovery_code(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """Spend one of the user's unused recovery codes; False if none matches. A code still
    kept as an unsalted SHA-256 (before backlog S16) is taken too and kept as HMAC from
    now on."""
    digest = _digest(code)
    keyed = _keyed(digest)
    recovery = await session.scalar(
        select(RecoveryCode)
        .where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash.in_([keyed, digest.hex()]),
            RecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery is None:
        return False
    recovery.code_hash = keyed
    recovery.used_at = datetime.now(UTC)
    return True


async def upgrade_legacy_recovery_codes(session: AsyncSession) -> int:
    """Key every recovery code still kept as an unsalted SHA-256; how many there were."""
    old = await session.scalars(
        select(RecoveryCode)
        .where(RecoveryCode.code_hash.not_like(f"{RECOVERY_HASH_PREFIX}%"))
        .with_for_update()
    )
    count = 0
    for recovery in old:
        recovery.code_hash = _keyed(bytes.fromhex(recovery.code_hash))
        count += 1
    await session.flush()
    return count

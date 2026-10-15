"""Two-factor login: TOTP (RFC 6238) and one-time recovery codes."""

import hashlib
import hmac
import secrets
import time
import uuid
from typing import TYPE_CHECKING

import pyotp
import segno

from app.core.crypto import seal, unseal

if TYPE_CHECKING:
    from app.models import User

ISSUER = "VisaCRM"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
# Codes from the previous and next 30-second step are accepted too: phone clocks drift.
_DRIFT_STEPS = 1
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


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
    if len(code) != 6 or not code.isdigit():
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


def hash_recovery_code(code: str) -> str:
    normalized = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256(normalized.encode()).hexdigest()

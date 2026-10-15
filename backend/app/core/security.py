import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()
# Verified against when the email is unknown, so a miss takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
# No 0/O, 1/l/I: temporary passwords are read off a screen and typed by hand.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def generate_temporary_password(length: int = 14) -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))


def new_session_token() -> tuple[str, str]:
    """Returns (token for the cookie, hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_session_token(token)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

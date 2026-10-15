"""Authenticated encryption for secrets (AES-256-GCM) under VAULT_MASTER_KEY.

Blob layout: version byte | 12-byte nonce | ciphertext with GCM tag. The associated data
(aad) binds a blob to its owner, e.g. a tenant id, so a blob copied onto another row
does not decrypt. Rotation: docs/operations.md.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_VERSION = 1
_NONCE_SIZE = 12
KEY_SIZE = 32


class VaultKeyError(RuntimeError):
    """The master key is missing or malformed, or no configured key opens a blob."""


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE_SIZE)
    return bytes([_VERSION]) + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    """Raises InvalidTag if the key or aad is wrong or the blob was tampered with."""
    if len(blob) <= 1 + _NONCE_SIZE or blob[0] != _VERSION:
        raise InvalidTag()
    nonce, ciphertext = blob[1 : 1 + _NONCE_SIZE], blob[1 + _NONCE_SIZE :]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def _decode_key(value: str, name: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise VaultKeyError(f"{name} is not valid base64") from error
    if len(key) != KEY_SIZE:
        raise VaultKeyError(f"{name} must be {KEY_SIZE} bytes, got {len(key)}")
    return key


def master_keys() -> list[bytes]:
    """Current master key first, then the previous one while a rotation is in progress."""
    settings = get_settings()
    if not settings.vault_master_key:
        raise VaultKeyError("VAULT_MASTER_KEY is not set")
    keys = [_decode_key(settings.vault_master_key, "VAULT_MASTER_KEY")]
    if settings.vault_master_key_previous:
        keys.append(_decode_key(settings.vault_master_key_previous, "VAULT_MASTER_KEY_PREVIOUS"))
    return keys


def seal(plaintext: bytes, aad: bytes) -> bytes:
    """Encrypt under the current master key."""
    return encrypt(master_keys()[0], plaintext, aad)


def unseal(blob: bytes, aad: bytes) -> bytes:
    """Decrypt with the current master key, or the previous one during a rotation."""
    for key in master_keys():
        try:
            return decrypt(key, blob, aad)
        except InvalidTag:
            continue
    raise VaultKeyError("No configured master key opens this secret")


def is_sealed_with_current_key(blob: bytes, aad: bytes) -> bool:
    try:
        decrypt(master_keys()[0], blob, aad)
    except InvalidTag:
        return False
    return True


def generate_master_key() -> str:
    return base64.b64encode(os.urandom(KEY_SIZE)).decode()

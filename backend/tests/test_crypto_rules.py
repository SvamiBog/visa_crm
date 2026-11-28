"""Encryption of secrets (app/core/crypto.py): AES-GCM bound to its owner, the master key
and its rotation. The workspace's key in the database is tests/test_crypto.py."""

import os

import pytest
from cryptography.exceptions import InvalidTag

from app.core.crypto import (
    VaultKeyError,
    decrypt,
    encrypt,
    generate_master_key,
    is_sealed_with_current_key,
    seal,
    unseal,
)

KEY = bytes(32)
OTHER_KEY = bytes([1]) * 32


def test_round_trip_and_random_nonce():
    first = encrypt(KEY, b"secret", b"owner-1")
    assert decrypt(KEY, first, b"owner-1") == b"secret"
    assert encrypt(KEY, b"secret", b"owner-1") != first
    assert b"secret" not in first


@pytest.mark.parametrize(
    ("key", "aad", "tamper"),
    [(OTHER_KEY, b"owner-1", False), (KEY, b"owner-2", False), (KEY, b"owner-1", True)],
    ids=["wrong key", "blob moved to another owner", "tampered blob"],
)
def test_decrypt_rejects(key: bytes, aad: bytes, tamper: bool):
    blob = bytearray(encrypt(KEY, b"secret", b"owner-1"))
    if tamper:
        blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(key, bytes(blob), aad)


@pytest.mark.parametrize(
    "value", ["", "not base64!", generate_master_key()[:20]], ids=["missing", "bad", "short"]
)
def test_master_key_must_be_valid(master_key, value: str):
    master_key(value)
    with pytest.raises(VaultKeyError):
        seal(b"x", b"aad")


def test_rotation_keeps_old_secrets_readable(master_key):
    old, new = generate_master_key(), generate_master_key()
    master_key(old)
    blob = seal(b"secret", b"aad")

    master_key(new, previous=old)
    assert unseal(blob, b"aad") == b"secret"
    assert not is_sealed_with_current_key(blob, b"aad")
    resealed = seal(unseal(blob, b"aad"), b"aad")

    master_key(new)
    assert unseal(resealed, b"aad") == b"secret"
    with pytest.raises(VaultKeyError):
        unseal(blob, b"aad")


def test_generated_master_key_is_32_bytes():
    import base64

    assert len(base64.b64decode(generate_master_key())) == 32
    assert os.environ["VAULT_MASTER_KEY"]  # tests run with a master key set by conftest

"""Per-tenant data keys for the password vault (vault entries arrive with backlog 1.7).

Each tenant has its own random AES key. It is stored only wrapped by the master key,
so a database dump alone does not reveal client passwords.
"""

import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import (
    KEY_SIZE,
    decrypt,
    encrypt,
    is_sealed_with_current_key,
    seal,
    unseal,
)
from app.core.two_factor import totp_secret_aad
from app.models import Tenant, User


def tenant_key_aad(tenant_id: uuid.UUID) -> bytes:
    return b"tenant-key:" + tenant_id.bytes


async def get_tenant_key(session: AsyncSession, tenant_id: uuid.UUID) -> bytes:
    """The tenant's data key, created on first use. The session must see the tenant row."""
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise LookupError("tenant not visible in this session")
    if tenant.vault_key_encrypted is None:
        key = os.urandom(KEY_SIZE)
        tenant.vault_key_encrypted = seal(key, tenant_key_aad(tenant_id))
        await session.flush()
        return key
    return unseal(tenant.vault_key_encrypted, tenant_key_aad(tenant_id))


def encrypt_for_tenant(key: bytes, tenant_id: uuid.UUID, plaintext: str) -> bytes:
    return encrypt(key, plaintext.encode(), b"vault:" + tenant_id.bytes)


def decrypt_for_tenant(key: bytes, tenant_id: uuid.UUID, blob: bytes) -> str:
    return decrypt(key, blob, b"vault:" + tenant_id.bytes).decode()


async def reseal_with_current_master_key(session: AsyncSession) -> int:
    """Re-encrypt tenant keys and TOTP secrets under the current master key (rotation step).

    Runs in the system scope. Returns how many secrets were re-encrypted.
    """
    count = 0
    tenants = await session.scalars(
        select(Tenant).where(Tenant.vault_key_encrypted.is_not(None)).with_for_update()
    )
    for tenant in tenants:
        assert tenant.vault_key_encrypted is not None
        aad = tenant_key_aad(tenant.id)
        if not is_sealed_with_current_key(tenant.vault_key_encrypted, aad):
            tenant.vault_key_encrypted = seal(unseal(tenant.vault_key_encrypted, aad), aad)
            count += 1
    users = await session.scalars(
        select(User).where(User.totp_secret_encrypted.is_not(None)).with_for_update()
    )
    for user in users:
        assert user.totp_secret_encrypted is not None
        aad = totp_secret_aad(user.id)
        if not is_sealed_with_current_key(user.totp_secret_encrypted, aad):
            user.totp_secret_encrypted = seal(unseal(user.totp_secret_encrypted, aad), aad)
            count += 1
    await session.flush()
    return count

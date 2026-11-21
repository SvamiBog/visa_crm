"""Per-tenant data keys for the password vault (app/models/vault.py).

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
    """The tenant's data key, created on first use. The session must see the tenant row.

    A key that exists is read without a lock. Reveals, files and an import's passwords
    must not hold the workspace row until their transaction ends (an import's lasts the
    whole run): a lock that stops the foreign key checks would stop every insert of the
    workspace (audit M6)."""
    row = (
        await session.execute(select(Tenant.vault_key_encrypted).where(Tenant.id == tenant_id))
    ).first()
    if row is None:
        raise LookupError("tenant not visible in this session")
    if row.vault_key_encrypted is not None:
        return unseal(row.vault_key_encrypted, tenant_key_aad(tenant_id))
    # First use: the row is locked so that one key is made. FOR NO KEY UPDATE lets the
    # workspace's inserts check their foreign keys meanwhile. populate_existing reads the
    # key again into a Tenant the session already holds (an import holds it from the
    # start): with the NULL it loaded, it would replace a key made in the meantime.
    tenant = await session.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if tenant is None:
        raise LookupError("tenant not visible in this session")
    if tenant.vault_key_encrypted is None:
        key = os.urandom(KEY_SIZE)
        tenant.vault_key_encrypted = seal(key, tenant_key_aad(tenant_id))
        await session.flush()
        return key
    return unseal(tenant.vault_key_encrypted, tenant_key_aad(tenant_id))


def _vault_aad(tenant_id: uuid.UUID, row_id: uuid.UUID) -> bytes:
    return b"vault:" + tenant_id.bytes + row_id.bytes


def encrypt_for_tenant(
    key: bytes, tenant_id: uuid.UUID, row_id: uuid.UUID, plaintext: str
) -> bytes:
    """Bound to the tenant and to the row it is stored in: copied elsewhere, it won't open."""
    return encrypt(key, plaintext.encode(), _vault_aad(tenant_id, row_id))


def decrypt_for_tenant(key: bytes, tenant_id: uuid.UUID, row_id: uuid.UUID, blob: bytes) -> str:
    return decrypt(key, blob, _vault_aad(tenant_id, row_id)).decode()


async def reseal_with_current_master_key(session: AsyncSession) -> int:
    """Re-encrypt tenant keys and TOTP secrets under the current master key (rotation step).

    Runs in the system scope. Returns how many secrets were re-encrypted. The rows are
    locked FOR NO KEY UPDATE: the rotation does not wait for the inserts that refer to them
    (an import's last its whole run), nor do those wait for it.
    """
    count = 0
    tenants = await session.scalars(
        select(Tenant)
        .where(Tenant.vault_key_encrypted.is_not(None))
        .with_for_update(key_share=True)
    )
    for tenant in tenants:
        assert tenant.vault_key_encrypted is not None
        aad = tenant_key_aad(tenant.id)
        if not is_sealed_with_current_key(tenant.vault_key_encrypted, aad):
            tenant.vault_key_encrypted = seal(unseal(tenant.vault_key_encrypted, aad), aad)
            count += 1
    users = await session.scalars(
        select(User).where(User.totp_secret_encrypted.is_not(None)).with_for_update(key_share=True)
    )
    for user in users:
        assert user.totp_secret_encrypted is not None
        aad = totp_secret_aad(user.id)
        if not is_sealed_with_current_key(user.totp_secret_encrypted, aad):
            user.totp_secret_encrypted = seal(unseal(user.totp_secret_encrypted, aad), aad)
            count += 1
    await session.flush()
    return count

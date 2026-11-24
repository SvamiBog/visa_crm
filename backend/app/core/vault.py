"""Per-tenant data keys for the password vault (app/models/vault.py).

Each tenant has its own random AES key. It is stored only wrapped by the master key,
so a database dump alone does not reveal client passwords. The same key encrypts the
stored files (app/core/documents.py, app/core/templates.py). A rotation of the master key
wraps it again (reseal_with_current_master_key); after a leak of the master key it is
replaced (rekey_workspace, backlog S16).
"""

import hmac
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import documents, portal, storage, templates
from app.core.crypto import (
    KEY_SIZE,
    decrypt,
    encrypt,
    is_sealed_with_current_key,
    master_keys,
    seal,
    unseal,
)
from app.core.two_factor import totp_secret_aad
from app.models import (
    ClientLink,
    ContactDocument,
    DocumentTemplate,
    PackageFile,
    Tenant,
    User,
    VaultEntry,
)


def tenant_key_aad(tenant_id: uuid.UUID) -> bytes:
    return b"tenant-key:" + tenant_id.bytes


async def get_tenant_key(session: AsyncSession, tenant_id: uuid.UUID) -> bytes:
    """The tenant's data key, created on first use. The session must see the tenant row.

    A key that exists is read FOR KEY SHARE, the weakest row lock. Reveals, files and an
    import's passwords hold it until their transaction ends (an import's lasts the whole
    run), and it stops nothing of the workspace (audit M6): the foreign key checks of its
    inserts take the same lock, and FOR NO KEY UPDATE (first use, rotation, any update of
    the row) goes with it. Only a rekey (FOR UPDATE, rekey_workspace) waits for the
    transactions holding the old key, and one asking meanwhile waits for the new key."""
    row = (
        await session.execute(
            select(Tenant.vault_key_encrypted)
            .where(Tenant.id == tenant_id)
            .with_for_update(read=True, key_share=True)
        )
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


class RekeyFailed(RuntimeError):
    """A stored file opens with neither the old nor the new key of its workspace."""


@dataclass(frozen=True)
class Rekeyed:
    vault_entries: int = 0
    files: int = 0


# (key, tenant_id, row_id, data) → data: opens or seals one stored file.
_FileCrypt = Callable[[bytes, uuid.UUID, uuid.UUID, bytes], bytes]
# The stored files a workspace key encrypts: the row, where its file lies, how it opens
# and how it is sealed.
_FILES: tuple[
    tuple[
        type[ContactDocument] | type[DocumentTemplate] | type[PackageFile],
        Callable[[uuid.UUID, uuid.UUID], str],
        _FileCrypt,
        _FileCrypt,
    ],
    ...,
] = (
    (ContactDocument, documents.object_key, documents.open_document, documents.seal_document),
    (DocumentTemplate, templates.object_key, templates.open_template, templates.seal_template),
    (PackageFile, documents.object_key, documents.open_document, documents.seal_document),
)


def _next_key(tenant_id: uuid.UUID, old: bytes) -> bytes:
    """The workspace's next key. Derived rather than random: a run stopped halfway leaves
    files under it while the database still holds the old key, and the next run must come
    to the same key without keeping it anywhere. It takes the current master key, so whoever
    kept only the old master key and the old workspace key cannot make it."""
    return hmac.new(master_keys()[0], b"rekey:" + tenant_id.bytes + old, "sha256").digest()


async def rekey_workspace(session: AsyncSession, tenant_id: uuid.UUID) -> Rekeyed:
    """Give the workspace a new data key and encrypt its vault passwords and stored files
    with it: the response to a leaked master key (audit L6, docs/operations.md). A rotation
    only wraps the same key again, so the old master key and a copy of the database from
    before it would open everything written after it. Runs in the system scope; the
    caller commits.

    The workspace row is held FOR UPDATE to the end: transactions holding the old key
    (get_tenant_key) finish first, and those asking meanwhile wait and get the new one.
    Files are encrypted again in place, one by one; a file that already opens with the new
    key was done by a run that stopped, and is left as it is.
    """
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise LookupError("tenant not visible in this session")
    if tenant.vault_key_encrypted is None:
        return Rekeyed()  # nothing was ever encrypted for it
    aad = tenant_key_aad(tenant_id)
    old = unseal(tenant.vault_key_encrypted, aad)
    new = _next_key(tenant_id, old)

    files = 0
    for model, object_key, open_file, seal_file in _FILES:
        ids = list(await session.scalars(select(model.id).where(model.tenant_id == tenant_id)))
        for row_id in ids:
            path = object_key(tenant_id, row_id)
            try:
                blob = await storage.get_object(path)
            except storage.StorageFailed as error:
                # Named, so that a file missing from the storage is found, not retried.
                raise storage.StorageFailed(f"{error} ({model.__tablename__} {row_id})") from error
            try:
                content = open_file(old, tenant_id, row_id, blob)
            except InvalidTag:
                try:
                    open_file(new, tenant_id, row_id, blob)
                except InvalidTag as error:
                    raise RekeyFailed(f"{model.__tablename__} {row_id}") from error
            else:
                await storage.put_object(path, seal_file(new, tenant_id, row_id, content))
            files += 1
            # A statement between files: the transaction would otherwise sit idle through
            # the whole storage work and be ended by idle_in_transaction_session_timeout.
            await session.execute(select(1))

    entries = (
        await session.execute(
            select(VaultEntry.id, VaultEntry.password_encrypted).where(
                VaultEntry.tenant_id == tenant_id, VaultEntry.password_encrypted.is_not(None)
            )
        )
    ).all()
    for entry_id, blob in entries:
        row_aad = _vault_aad(tenant_id, entry_id)
        await session.execute(
            update(VaultEntry)
            .where(VaultEntry.id == entry_id)
            # Kept: a new key is not a change of the account.
            .values(
                password_encrypted=encrypt(new, decrypt(old, blob, row_aad), row_aad),
                updated_at=VaultEntry.updated_at,
            )
        )
    # The client portal's links (backlog 2.9): kept to be copied again.
    links = (
        await session.execute(
            select(ClientLink.id, ClientLink.token_sealed, ClientLink.code_sealed).where(
                ClientLink.tenant_id == tenant_id
            )
        )
    ).all()
    for link_id, blob, code_blob in links:
        token = portal.open_link_token(old, tenant_id, link_id, blob)
        values = {"token_sealed": portal.seal_link_token(new, tenant_id, link_id, token)}
        if code_blob is not None:
            code = portal.open_code(old, tenant_id, link_id, code_blob)
            values["code_sealed"] = portal.seal_code(new, tenant_id, link_id, code)
        await session.execute(update(ClientLink).where(ClientLink.id == link_id).values(**values))
    tenant.vault_key_encrypted = seal(new, aad)
    await session.flush()
    return Rekeyed(vault_entries=len(entries), files=files)


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

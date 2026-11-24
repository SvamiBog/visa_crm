"""Erasing a workspace's data when the specialist leaves for another system (docs/decisions.md
→ «Удаление всех данных кабинета»). Every row with the workspace's tenant_id goes, except
the tables in KEPT_TABLES; the caller removes the files from the storage afterwards.

Tables are found by their tenant_id column, so a new business table is erased with the rest
without a change here; keeping one needs a line in KEPT_TABLES.
"""

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import documents, templates
from app.core.db import Base
from app.core.dictionaries import add_default_dictionaries
from app.models import ContactDocument, DocumentTemplate, PackageFile, Tenant, User, UserRole

# Kept on purpose: the workspace itself with its settings (`tenants` has no tenant_id), the
# owner's account (employees go, with their sessions and codes), the subscription payments
# the admin recorded, and the audit log, which nobody may change.
KEPT_TABLES = frozenset({"users", "subscription_payments", "audit_events"})


async def erase_workspace(session: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """Delete the workspace's data in the session's transaction; returns the storage keys of
    its files. The session should be scoped to the tenant: RLS then keeps every statement
    inside it whatever the filter says."""
    keys = (
        [
            documents.object_key(tenant_id, document_id)
            for document_id in await session.scalars(
                select(ContactDocument.id).where(ContactDocument.tenant_id == tenant_id)
            )
        ]
        + [
            templates.object_key(tenant_id, template_id)
            for template_id in await session.scalars(
                select(DocumentTemplate.id).where(DocumentTemplate.tenant_id == tenant_id)
            )
        ]
        + [
            # Files of the client portal lie under the same keys as the card's (backlog 2.9).
            documents.object_key(tenant_id, file_id)
            for file_id in await session.scalars(
                select(PackageFile.id).where(PackageFile.tenant_id == tenant_id)
            )
        ]
    )
    # Children before parents: no foreign key stops a delete.
    for table in reversed(Base.metadata.sorted_tables):
        if "tenant_id" in table.c and table.name not in KEPT_TABLES:
            await session.execute(delete(table).where(table.c.tenant_id == tenant_id))
    await session.execute(
        delete(User).where(User.tenant_id == tenant_id, User.role == UserRole.EMPLOYEE)
    )
    # The vault key goes with the secrets it sealed: copies of them left in backups or in
    # the storage no longer open with this database. The next secret gets a new key.
    await session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(vault_key_encrypted=None, max_deleted_case_number=0)
    )
    # The workspace starts again like a new one, its cases numbered from №1.
    add_default_dictionaries(session, tenant_id)
    return keys

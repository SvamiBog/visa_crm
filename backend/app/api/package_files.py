"""Files at the items of a case's checklist (backlog 2.9): taking one into the storage and
removing them. A file is encrypted with the workspace key and bound to its row like the
consent and contract files of the card (app/core/documents.py: the same sealing and the
same `documents/<tenant>/<id>` keys, ids only); the workspace's space is counted over both.
"""

import logging
import uuid
from collections.abc import Iterable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.documents import DocumentError, content_type_of, object_key, seal_document
from app.core.portal import MAX_FILE_BYTES, MAX_FILES_PER_CASE, MAX_FILES_PER_ITEM
from app.models import ContactDocument, PackageFile

logger = logging.getLogger(__name__)

# The workspace's room for files (backlog S12): the card's files and the portal's together.
MAX_WORKSPACE_BYTES = 10 * 1024 * 1024 * 1024


def checked_type(data: bytes) -> str:
    """The file's type by its content, or the API error: PDF and photos up to 20 MB."""
    try:
        return content_type_of(data, MAX_FILE_BYTES)
    except DocumentError as error:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code == "document_too_large"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        # The card's files have their own limit and text (10 MB); the portal takes 20.
        detail = "document_too_large_20" if error.code == "document_too_large" else error.code
        raise HTTPException(code, detail) from error


async def check_room(system: AsyncSession, file: PackageFile, *, commit: bool = True) -> None:
    """Refuse a file past the item's, the case's or the workspace's quota. Counted in the
    system scope for the file's workspace: an employee sees only their cases' files, while
    the space is everyone's. Numbers only come out."""
    item = (
        PackageFile.checklist_item_id == file.checklist_item_id
        if file.checklist_item_id
        else PackageFile.own_item_id == file.own_item_id
    )
    on_item, on_case, portal_bytes = (
        await system.execute(
            select(
                func.count().filter(PackageFile.case_id == file.case_id, item),
                func.count().filter(PackageFile.case_id == file.case_id),
                func.coalesce(func.sum(PackageFile.size_bytes), 0),
            ).where(PackageFile.tenant_id == file.tenant_id)
        )
    ).one()
    card_bytes = await system.scalar(
        select(func.coalesce(func.sum(ContactDocument.size_bytes), 0)).where(
            ContactDocument.tenant_id == file.tenant_id
        )
    )
    if commit:
        # End the read: the connection is not kept through the upload.
        await system.commit()
    if on_item >= MAX_FILES_PER_ITEM:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_item_files")
    if on_case >= MAX_FILES_PER_CASE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_case_files")
    if portal_bytes + card_bytes + file.size_bytes > MAX_WORKSPACE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "documents_storage_full")


def storage_error(error: Exception) -> HTTPException:
    if isinstance(error, storage.StorageNotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed")


async def put_file(
    session: AsyncSession, system: AsyncSession, file: PackageFile, key: bytes, data: bytes
) -> None:
    """Send the file and add its row in `session` (not committed). The quota is counted
    before the upload, so a file past it never reaches the storage, and again under a lock
    of the workspace after it: uploads sent at once would all count the same files.
    A file that lost that race, or whose case went meanwhile, leaves the storage."""
    if not storage.is_configured():
        raise storage_error(storage.StorageNotConfigured())
    await check_room(system, file)
    path = object_key(file.tenant_id, file.id)
    try:
        await storage.put_object(path, seal_document(key, file.tenant_id, file.id, data))
    except (storage.StorageNotConfigured, storage.StorageFailed) as error:
        raise storage_error(error) from error
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"contact_documents:{file.tenant_id}"},
    )
    try:
        await check_room(system, file)
    except HTTPException:
        await remove_objects([path], "refused")
        raise
    session.add(file)
    try:
        await session.flush()
    except Exception:
        await session.rollback()
        await remove_objects([path], "orphaned")
        raise


async def package_file_keys(session: AsyncSession, case_ids: Sequence[uuid.UUID]) -> list[str]:
    """The storage keys of the cases' portal files, read before the cases are deleted."""
    if not case_ids:
        return []
    rows = await session.execute(
        select(PackageFile.tenant_id, PackageFile.id).where(PackageFile.case_id.in_(case_ids))
    )
    return [object_key(tenant_id, file_id) for tenant_id, file_id in rows]


async def remove_objects(keys: Iterable[str], why: str) -> None:
    """Remove stored files whose rows are gone or were never kept."""
    for key in keys:
        try:
            await storage.delete_object(key)
        except (storage.StorageNotConfigured, storage.StorageFailed):
            # No row refers to it: what is left is ciphertext nobody can open through the
            # app. The key holds ids only.
            logger.warning("Object %s of a %s file was not removed", key, why)

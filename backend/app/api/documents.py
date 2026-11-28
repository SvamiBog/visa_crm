"""Files of the consent and the contract in the client's card (backlog 1.2). Closed by the
«clients» module, like the card itself.

A file is encrypted with the workspace's key before it goes to the storage
(app/core/documents.py, app/core/storage.py). Every download is written to the audit log;
names of files stay out of it and out of URLs (they may carry the client's name).
A client keeps a limited number of files and a workspace a limited number of bytes
(backlog S12), so no member can fill the storage.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Base64Bytes, BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SystemSession, TenantSession, require_module
from app.core.audit import record
from app.core.documents import (
    DocumentError,
    clean_file_name,
    content_disposition,
    content_type_of,
    object_key,
    open_document,
    seal_document,
)
from app.core.employee_limits import LimitKind, open_card, watch
from app.core.storage import (
    StorageFailed,
    StorageNotConfigured,
    delete_object,
    get_object,
    is_configured,
    put_object,
)
from app.core.vault import get_tenant_key
from app.models import AuditAction, Contact, ContactDocument, DocumentKind, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(require_module("clients"))]
)

Member = Annotated[CurrentUser, Depends(require_module("clients"))]

# Quotas (owner's decision, 2026-11-22). A client: a consent and a contract per trip,
# photographed page by page, for someone who comes back for years. A workspace: years of
# such clients (about 3,500 at two files of 1.5 MB). Templates have their own limit. The
# numbers are also in the texts of the errors (errors.* in the locales).
MAX_DOCUMENTS_PER_CONTACT = 50
MAX_WORKSPACE_BYTES = 10 * 1024 * 1024 * 1024


class DocumentIn(BaseModel):
    kind: DocumentKind
    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Base64Bytes


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: DocumentKind
    file_name: str
    content_type: str
    size_bytes: int
    uploaded_by: uuid.UUID | None
    uploaded_by_email: str | None
    created_at: datetime


def _out(document: ContactDocument, uploaded_by_email: str | None) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        kind=document.kind,
        file_name=document.file_name,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        uploaded_by=document.uploaded_by,
        uploaded_by_email=uploaded_by_email,
        created_at=document.created_at,
    )


async def documents_of(session: AsyncSession, contact_id: uuid.UUID) -> list[DocumentOut]:
    """The contact's files, newest first."""
    rows = await session.execute(
        select(ContactDocument, User.email)
        .outerjoin(User, User.id == ContactDocument.uploaded_by)
        .where(ContactDocument.contact_id == contact_id)
        .order_by(ContactDocument.created_at.desc(), ContactDocument.id.desc())
    )
    return [_out(document, email) for document, email in rows]


def _audit(
    session: AsyncSession, action: AuditAction, user: CurrentUser, document: ContactDocument
) -> None:
    """The id and kind only: the file name may carry the client's name."""
    record(
        session,
        action,
        actor=user,
        target_type="contact",
        target_id=document.contact_id,
        details={"document": str(document.id), "kind": document.kind.value},
    )


async def _document(
    session: AsyncSession, contact_id: uuid.UUID, document_id: uuid.UUID
) -> ContactDocument:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    document = await session.get(ContactDocument, document_id)
    if document is None or document.contact_id != contact_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return document


def _storage_error(error: Exception) -> HTTPException:
    if isinstance(error, StorageNotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed")


async def _check_room(
    system: AsyncSession, tenant_id: uuid.UUID, contact_id: uuid.UUID, size: int
) -> None:
    """Refuse a file past the client's or the workspace's quota. Counted in the system
    scope, for the user's own workspace only: an employee sees just their clients' files,
    while the workspace's space is taken by everyone's. Two numbers come out, no rows."""
    count, used = (
        await system.execute(
            select(
                func.count().filter(ContactDocument.contact_id == contact_id),
                func.coalesce(func.sum(ContactDocument.size_bytes), 0),
            ).where(ContactDocument.tenant_id == tenant_id)
        )
    ).one()
    # End the read: the connection is not kept through the upload.
    await system.commit()
    if count >= MAX_DOCUMENTS_PER_CONTACT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_documents")
    if used + size > MAX_WORKSPACE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "documents_storage_full")


async def _remove_object(document: ContactDocument, why: str) -> None:
    """Remove the file of a row that is gone or was never kept."""
    try:
        await delete_object(object_key(document.tenant_id, document.id))
    except (StorageNotConfigured, StorageFailed):
        # No row refers to it: what is left in the storage is ciphertext nobody can open
        # through the app.
        logger.warning("Object of %s document %s was not removed", why, document.id)


@router.post("/{contact_id}/documents", status_code=status.HTTP_201_CREATED)
async def add_document(
    contact_id: uuid.UUID,
    body: DocumentIn,
    user: Member,
    session: TenantSession,
    system: SystemSession,
) -> DocumentOut:
    assert user.tenant_id is not None
    if await session.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    await open_card(session, user, contact_id)  # a file of the card (Т.14)
    if not is_configured():
        raise _storage_error(StorageNotConfigured())
    try:
        content_type = content_type_of(body.content)
    except DocumentError as error:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code == "document_too_large"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, error.code) from error
    document = ContactDocument(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        contact_id=contact_id,
        kind=body.kind,
        file_name=clean_file_name(body.file_name, content_type),
        content_type=content_type,
        size_bytes=len(body.content),
        uploaded_by=user.id,
    )
    key = await get_tenant_key(session, user.tenant_id)
    # The workspace row stays locked until commit: release it (keeping a key made on first
    # use) before the upload, which may take seconds.
    await session.commit()
    # Before sending: a file past the quota never reaches the storage.
    await _check_room(system, user.tenant_id, contact_id, document.size_bytes)
    try:
        await put_object(
            object_key(user.tenant_id, document.id),
            seal_document(key, user.tenant_id, document.id, body.content),
        )
    except (StorageNotConfigured, StorageFailed) as error:
        raise _storage_error(error) from error
    # One file at a time per workspace from the count to the commit: uploads sent at once
    # would all count the same files and all pass. The lock is not held through the
    # upload (a wait for it gives up after LOCK_TIMEOUT), so the count is made again here;
    # a file that lost the race to another goes from the storage.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"contact_documents:{user.tenant_id}"},
    )
    try:
        await _check_room(system, user.tenant_id, contact_id, document.size_bytes)
    except HTTPException:
        await _remove_object(document, "refused")
        raise
    session.add(document)
    try:
        await session.flush()
    except IntegrityError as error:
        # The card was deleted as a duplicate while the file was on its way (it had no
        # file, so it could be): no row will refer to the object, so it goes too.
        await session.rollback()
        await _remove_object(document, "orphaned")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from error
    _audit(session, AuditAction.CONTACT_DOCUMENT_ADDED, user, document)
    await session.commit()
    await session.refresh(document)
    return _out(document, user.email)


@router.get("/{contact_id}/documents/{document_id}")
async def download_document(
    contact_id: uuid.UUID, document_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """The file itself, always as a download. Every call is written to the audit log."""
    assert user.tenant_id is not None
    document = await _document(session, contact_id, document_id)
    # A file of the card opens the card for an employee's daily limit (Т.14).
    await watch(session, user, LimitKind.CARDS, "contact", contact_id, AuditAction.CONTACT_VIEWED)
    try:
        blob = await get_object(object_key(user.tenant_id, document.id))
    except (StorageNotConfigured, StorageFailed) as error:
        raise _storage_error(error) from error
    key = await get_tenant_key(session, user.tenant_id)
    try:
        content = open_document(key, user.tenant_id, document.id, blob)
    except InvalidTag as error:
        logger.error("Document %s does not decrypt", document.id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed") from error
    _audit(session, AuditAction.CONTACT_DOCUMENT_OPENED, user, document)
    await session.commit()
    return Response(
        content=content,
        media_type=document.content_type,
        headers={"Content-Disposition": content_disposition(document.file_name)},
    )


@router.delete("/{contact_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    contact_id: uuid.UUID, document_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """Employees delete only files they added; the specialist deletes any."""
    assert user.tenant_id is not None
    document = await _document(session, contact_id, document_id)
    await open_card(session, user, contact_id)
    if user.role != UserRole.SPECIALIST and document.uploaded_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    await session.delete(document)
    _audit(session, AuditAction.CONTACT_DOCUMENT_DELETED, user, document)
    await session.commit()
    await _remove_object(document, "deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

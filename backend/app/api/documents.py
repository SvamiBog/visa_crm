"""Files of the consent and the contract in the client's card (backlog 1.2). Closed by the
«clients» module, like the card itself.

A file is encrypted with the workspace's key before it goes to the storage
(app/core/documents.py, app/core/storage.py). Every download is written to the audit log;
names of files stay out of it and out of URLs (they may carry the client's name).
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Base64Bytes, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module
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
    return [_out(document, email) for document, email in rows.tuples()]


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


@router.post("/{contact_id}/documents", status_code=status.HTTP_201_CREATED)
async def add_document(
    contact_id: uuid.UUID, body: DocumentIn, user: Member, session: TenantSession
) -> DocumentOut:
    assert user.tenant_id is not None
    if await session.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
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
    try:
        await put_object(
            object_key(user.tenant_id, document.id),
            seal_document(key, user.tenant_id, document.id, body.content),
        )
    except (StorageNotConfigured, StorageFailed) as error:
        raise _storage_error(error) from error
    session.add(document)
    await session.flush()
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
    if user.role != UserRole.SPECIALIST and document.uploaded_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    await session.delete(document)
    _audit(session, AuditAction.CONTACT_DOCUMENT_DELETED, user, document)
    await session.commit()
    try:
        await delete_object(object_key(user.tenant_id, document.id))
    except (StorageNotConfigured, StorageFailed):
        # The row is gone; what is left in the storage is ciphertext nobody refers to.
        logger.warning("Object of deleted document %s was not removed", document.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

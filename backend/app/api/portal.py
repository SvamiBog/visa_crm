"""The client portal in the case (backlog 2.9; docs/decisions.md → «Портал клиента и вход
по коду»): the link the specialist copies, the code she sees or sends by email,
the files at the items of the checklist with «Принять» and «Вернуть», and her own uploads.
The client's side is app/api/portal_public.py, the rules app/core/portal.py.

Closed by the «clients», «cases» and «package_check» modules, like the package check. An
employee works with the portal of the cases given to them (employee_scope); opening it or
downloading a file counts as opening the client's card for the daily limit (Т.14).
The audit log gets ids and channels only: no file names, no codes.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Base64Bytes, BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SystemSession, TenantSession, require_module
from app.api.package import (
    PackageFileOut,
    case_of,
    check_document_date,
    checklist_item,
    file_out,
    mark_of,
)
from app.api.package_files import checked_type, put_file, remove_objects, storage_error
from app.core import portal, storage
from app.core.audit import record
from app.core.days import Day
from app.core.documents import (
    clean_file_name,
    content_disposition,
    object_key,
    open_document,
)
from app.core.mail import Mail, MailFailed, MailNotConfigured, send_mail
from app.core.vault import get_tenant_key
from app.models import (
    AuditAction,
    AuditEvent,
    ClientLink,
    ClientSession,
    CodeChannel,
    ConsentGivenBy,
    Contact,
    FileSource,
    FileStatus,
    OwnChecklistItem,
    PackageFile,
    PackageItemState,
    PortalConsent,
    PortalNotice,
    ReturnReason,
    Tenant,
    UserRole,
    VisaCase,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["portal"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("cases")),
        Depends(require_module("package_check")),
    ],
)

Member = Annotated[CurrentUser, Depends(require_module("package_check"))]


# --- Schemas -------------------------------------------------------------------------


class ConsentOut(BaseModel):
    given_at: datetime
    notice_version: str
    special_categories: bool
    given_by: ConsentGivenBy
    representative_name: str | None
    code_channel: CodeChannel


class PortalOut(BaseModel):
    # False once the documents are handed over (review, result): the link no longer works.
    open: bool
    token: str | None
    created_at: datetime | None
    # The link's code, always shown to the specialist, and how it last went to the client.
    code: str | None
    code_channel: CodeChannel | None
    # Five wrong tries in a row: the client needs a new code.
    code_blocked: bool
    has_email: bool
    # The specialist's details as the data controller are filled in («Настройки»).
    controller_ready: bool
    consent: ConsentOut | None


class CodeIn(BaseModel):
    # specialist: «Новый код», shown here to pass on; email: the link's code to the email of
    # the card (a new one only when there is none, or with `new`).
    channel: CodeChannel
    new: bool = False


class CodeOut(BaseModel):
    channel: CodeChannel
    code: str


class StaffUploadIn(BaseModel):
    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Base64Bytes
    document_date: Day | None = None


class AcceptIn(BaseModel):
    document_date: Day | None = None


class ReturnIn(BaseModel):
    reason: ReturnReason
    note: Annotated[str, Field(max_length=portal.MAX_RETURN_NOTE)] | None = None

    @model_validator(mode="after")
    def other_needs_text(self) -> "ReturnIn":
        self.note = (self.note or "").strip() or None
        if not portal.return_reason_valid(self.reason, self.note):
            raise ValueError("note_required")
        return self


# --- The link and the code (shared with the client's side) -------------------------


def case_open(case: VisaCase) -> bool:
    return portal.link_open(case.stage, case.outcome, None)


async def live_link(session: AsyncSession, case_id: uuid.UUID) -> ClientLink | None:
    return await session.scalar(
        select(ClientLink).where(ClientLink.case_id == case_id, ClientLink.revoked_at.is_(None))
    )


async def issue_link(
    session: AsyncSession, case: VisaCase, user: CurrentUser | None
) -> tuple[ClientLink, str]:
    """A new link for the case (not committed)."""
    key = await get_tenant_key(session, case.tenant_id)
    token = portal.new_link_token()
    link = ClientLink(
        id=uuid.uuid4(),
        tenant_id=case.tenant_id,
        case_id=case.id,
        token_hash=portal.link_token_hash(token),
        created_by=user.id if user else None,
    )
    link.token_sealed = portal.seal_link_token(key, case.tenant_id, link.id, token)
    session.add(link)
    await session.flush()
    audit_case(session, AuditAction.PORTAL_LINK_ISSUED, user, case, {"link": str(link.id)})
    return link, token


async def ensure_link(
    session: AsyncSession, case: VisaCase, user: CurrentUser | None
) -> tuple[ClientLink, str]:
    """The case's live link and its token, made on first need (committed). Two requests
    making it at once (two tabs): the unique live link per case lets one win, the other
    reads the winner's."""
    case_id, tenant_id = case.id, case.tenant_id
    link = await live_link(session, case_id)
    if link is None:
        try:
            link, token = await issue_link(session, case, user)
            await session.commit()
            return link, token
        except IntegrityError:
            await session.rollback()
            await session.refresh(case)  # the rollback expired it; the caller goes on with it
            link = await live_link(session, case_id)
            if link is None:
                raise
    key = await get_tenant_key(session, tenant_id)
    return link, portal.open_link_token(key, tenant_id, link.id, link.token_sealed)


def audit_case(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser | None,
    case: VisaCase,
    details: dict[str, str],
) -> None:
    """Ids and channels only. A client's action has no account: it names the link."""
    record(
        session,
        action,
        actor=user,
        tenant_id=case.tenant_id,
        target_type="case",
        target_id=case.id,
        details={"contact": str(case.contact_id)} | details,
    )


async def codes_today(system: AsyncSession, link: ClientLink, now: datetime) -> int:
    """Codes the link got in the last 24 hours, by any channel: counted in the system scope
    (an employee's session sees only their own events)."""
    count = await system.scalar(
        select(func.count()).where(
            AuditEvent.tenant_id == link.tenant_id,
            AuditEvent.action == AuditAction.PORTAL_CODE_ISSUED,
            AuditEvent.details["link"].astext == str(link.id),
            AuditEvent.occurred_at > now - timedelta(days=1),
        )
    )
    return count or 0


def code_letter(to: str, code: str) -> Mail:
    return Mail(
        to=to,
        subject="Код для загрузки документов / Your code to upload documents",
        text=(
            f"Код для входа на страницу загрузки документов: {code}\n"
            "Он действует, пока вы собираете документы. Если вы не просили код, "
            "не отвечайте на письмо.\n\n"
            f"Your code to open the document upload page: {code}\n"
            "It stays valid while you collect your documents. If you did not ask for it, "
            "ignore this letter.\n"
        ),
    )


async def issue_code(
    session: AsyncSession,
    system: AsyncSession,
    link: ClientLink,
    case: VisaCase,
    channel: CodeChannel,
    user: CurrentUser | None,
    email: str | None = None,
    *,
    new: bool,
) -> str:
    """The link's code, a new one when asked or when there is none (it replaces the old
    one and unblocks); sends the letter for the email channel. Committed. 429 within a
    minute of the last code or past the day's codes."""
    now = datetime.now(UTC)
    # One issue at a time per link: requests sent at once would all pass the checks.
    locked = await session.scalar(
        select(ClientLink).where(ClientLink.id == link.id).with_for_update()
    )
    assert locked is not None
    sent_today = await codes_today(system, locked, now)
    await system.commit()
    if not portal.may_send_code(locked.code_sent_at, sent_today=sent_today, now=now):
        code = "too_many_codes" if sent_today >= portal.MAX_CODES_A_DAY else "code_sent_recently"
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, code)
    key = await get_tenant_key(session, locked.tenant_id)
    new = new or locked.code_sealed is None
    if new:
        code = portal.new_code()
        locked.code_hash = portal.code_hash(locked.id, code)
        locked.code_sealed = portal.seal_code(key, locked.tenant_id, locked.id, code)
        locked.code_attempts = 0
        locked.code_issued_by = user.id if user else None
    else:
        assert locked.code_sealed is not None
        code = portal.open_code(key, locked.tenant_id, locked.id, locked.code_sealed)
    locked.code_channel = channel
    locked.code_sent_at = now
    audit_case(
        session,
        AuditAction.PORTAL_CODE_ISSUED,
        user,
        case,
        {"link": str(locked.id), "channel": channel.value, "new": str(new).lower()},
    )
    if channel == CodeChannel.EMAIL:
        assert email is not None
        try:
            await send_mail(code_letter(email, code))
        except (MailNotConfigured, MailFailed) as error:
            await session.rollback()
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "mail_unavailable") from error
    await session.commit()
    return code


# --- Endpoints: the link and the code -----------------------------------------------


async def _portal_out(session: AsyncSession, case: VisaCase, user: CurrentUser) -> PortalOut:
    """The case's portal; a case still open gets its link on first look (a case made
    before the portal, or one nobody has opened since)."""
    contact = await session.get_one(Contact, case.contact_id)
    tenant = await session.get_one(Tenant, case.tenant_id)
    consent = await session.scalar(
        select(PortalConsent)
        .where(PortalConsent.case_id == case.id)
        .order_by(PortalConsent.given_at.desc())
        .limit(1)
    )
    consent_out = None
    if consent is not None:
        notice = await session.get_one(PortalNotice, consent.notice_id)
        consent_out = ConsentOut(
            given_at=consent.given_at,
            notice_version=notice.version,
            special_categories=consent.special_categories,
            given_by=consent.given_by,
            representative_name=consent.representative_name,
            code_channel=consent.code_channel,
        )
    common = {
        "has_email": bool(contact.email),
        "controller_ready": bool(
            tenant.controller_name and tenant.controller_address and tenant.controller_email
        ),
        "consent": consent_out,
    }
    if not case_open(case):
        # Handed over for review: the link and its code go (the public side refuses them
        # already, whether or not anyone has looked here).
        link = await live_link(session, case.id)
        if link is not None:
            await _revoke(session, case, link, user, "closed")
            await session.commit()
        return PortalOut(
            open=False,
            token=None,
            created_at=None,
            code=None,
            code_channel=None,
            code_blocked=False,
            **common,
        )
    link, token = await ensure_link(session, case, user)
    code = None
    if link.code_sealed is not None:
        key = await get_tenant_key(session, case.tenant_id)
        code = portal.open_code(key, case.tenant_id, link.id, link.code_sealed)
    return PortalOut(
        open=True,
        token=token,
        created_at=link.created_at,
        code=code,
        code_channel=link.code_channel,
        code_blocked=code is not None and link.code_attempts >= portal.MAX_CODE_ATTEMPTS,
        **common,
    )


async def _revoke(
    session: AsyncSession, case: VisaCase, link: ClientLink, user: CurrentUser, why: str
) -> None:
    """The link stops working with its code and the client's sessions (not committed)."""
    link.revoked_at = datetime.now(UTC)
    link.code_hash = None
    link.code_sealed = None
    # The client's sessions go at once, not only on their next request.
    for row in await session.scalars(select(ClientSession).where(ClientSession.link_id == link.id)):
        await session.delete(row)
    await session.flush()
    audit_case(
        session, AuditAction.PORTAL_LINK_REVOKED, user, case, {"link": str(link.id), "why": why}
    )


@router.get("/cases/{case_id}/portal")
async def get_portal(case_id: uuid.UUID, user: Member, session: TenantSession) -> PortalOut:
    case = await case_of(session, user, case_id)
    return await _portal_out(session, case, user)


@router.post("/cases/{case_id}/portal/revoke")
async def revoke_link(case_id: uuid.UUID, user: Member, session: TenantSession) -> PortalOut:
    """The link stops working with its sessions and the code waiting; a new one is made."""
    case = await case_of(session, user, case_id)
    if not case_open(case):
        raise HTTPException(status.HTTP_409_CONFLICT, "portal_closed")
    link = await live_link(session, case.id)
    if link is not None:
        await _revoke(session, case, link, user, "revoked")
    await issue_link(session, case, user)
    await session.commit()
    return await _portal_out(session, case, user)


@router.post("/cases/{case_id}/portal/code")
async def new_code(
    case_id: uuid.UUID, body: CodeIn, user: Member, session: TenantSession, system: SystemSession
) -> CodeOut:
    """«Новый код» for the specialist to pass on, or the link's code to the email of the
    card."""
    case = await case_of(session, user, case_id)
    if not case_open(case):
        raise HTTPException(status.HTTP_409_CONFLICT, "portal_closed")
    if body.channel == CodeChannel.TELEGRAM:
        # The bot that sends it is a later task.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "channel_unavailable")
    email = None
    if body.channel == CodeChannel.EMAIL:
        email = (await session.get_one(Contact, case.contact_id)).email
        if not email:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_email")
    link, _ = await ensure_link(session, case, user)
    new = body.new or body.channel == CodeChannel.SPECIALIST
    code = await issue_code(session, system, link, case, body.channel, user, email, new=new)
    return CodeOut(channel=body.channel, code=code)


# --- Endpoints: files at the items ---------------------------------------------------


async def _file(session: AsyncSession, case: VisaCase, file_id: uuid.UUID) -> PackageFile:
    """RLS hides other workspaces' rows and employee_scope cases not given."""
    file = await session.get(PackageFile, file_id)
    if file is None or file.case_id != case.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return file


async def _accept(
    session: AsyncSession,
    case: VisaCase,
    file: PackageFile,
    user: CurrentUser,
    document_date: date | None,
) -> None:
    """The file is accepted and its item is «есть» with the document's date."""
    await check_document_date(session, case, document_date)
    item_id = file.checklist_item_id or file.own_item_id
    assert item_id is not None
    item = await checklist_item(session, case, item_id)
    mark = await mark_of(session, case, item)
    mark.state = PackageItemState.PRESENT
    mark.document_date = document_date
    file.status = FileStatus.ACCEPTED
    file.return_reason = None
    file.return_note = None
    file.reviewed_by = user.id
    file.reviewed_at = datetime.now(UTC)


@router.post("/cases/{case_id}/package/{item_id}/files", status_code=status.HTTP_201_CREATED)
async def add_file(
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    body: StaffUploadIn,
    user: Member,
    session: TenantSession,
    system: SystemSession,
) -> PackageFileOut:
    """A document the client sent in a messenger or brought: accepted at once, the item
    becomes «есть» (owner's decision 2026-11-24)."""
    case = await case_of(session, user, case_id)
    item = await checklist_item(session, case, item_id)
    await check_document_date(session, case, body.document_date)
    content_type = checked_type(body.content)
    own = isinstance(item, OwnChecklistItem)
    file = PackageFile(
        id=uuid.uuid4(),
        tenant_id=case.tenant_id,
        case_id=case.id,
        checklist_item_id=None if own else item.id,
        own_item_id=item.id if own else None,
        file_name=clean_file_name(body.file_name, content_type),
        content_type=content_type,
        size_bytes=len(body.content),
        source=FileSource.STAFF,
        status=FileStatus.PENDING,
        uploaded_by=user.id,
    )
    key = await get_tenant_key(session, case.tenant_id)
    # Release the workspace row (kept locked when a key was made) before the upload.
    await session.commit()
    await put_file(session, system, file, key, body.content)
    await _accept(session, case, file, user, body.document_date)
    audit_case(session, AuditAction.PORTAL_FILE_UPLOADED, user, case, {"file": str(file.id)})
    await session.commit()
    await session.refresh(file)
    return file_out(file, user.email)


@router.get("/cases/{case_id}/package-files/{file_id}")
async def download_file(
    case_id: uuid.UUID, file_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """The file itself, always as a download; every call is in the audit log."""
    case = await case_of(session, user, case_id)
    file = await _file(session, case, file_id)
    try:
        blob = await storage.get_object(object_key(case.tenant_id, file.id))
    except (storage.StorageNotConfigured, storage.StorageFailed) as error:
        raise storage_error(error) from error
    key = await get_tenant_key(session, case.tenant_id)
    try:
        content = open_document(key, case.tenant_id, file.id, blob)
    except InvalidTag as error:
        logger.error("Package file %s does not decrypt", file.id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed") from error
    audit_case(session, AuditAction.PORTAL_FILE_OPENED, user, case, {"file": str(file.id)})
    await session.commit()
    return Response(
        content=content,
        media_type=file.content_type,
        headers={"Content-Disposition": content_disposition(file.file_name)},
    )


@router.post("/cases/{case_id}/package-files/{file_id}/accept")
async def accept_file(
    case_id: uuid.UUID, file_id: uuid.UUID, body: AcceptIn, user: Member, session: TenantSession
) -> PackageFileOut:
    case = await case_of(session, user, case_id)
    file = await _file(session, case, file_id)
    await _accept(session, case, file, user, body.document_date)
    audit_case(session, AuditAction.PORTAL_FILE_ACCEPTED, user, case, {"file": str(file.id)})
    await session.commit()
    await session.refresh(file)
    return file_out(file, None)


@router.post("/cases/{case_id}/package-files/{file_id}/return")
async def return_file(
    case_id: uuid.UUID, file_id: uuid.UUID, body: ReturnIn, user: Member, session: TenantSession
) -> PackageFileOut:
    """Sent back with a reason the client sees; the client uploads a new one."""
    case = await case_of(session, user, case_id)
    file = await _file(session, case, file_id)
    file.status = FileStatus.RETURNED
    file.return_reason = body.reason
    file.return_note = body.note
    file.reviewed_by = user.id
    file.reviewed_at = datetime.now(UTC)
    audit_case(
        session,
        AuditAction.PORTAL_FILE_RETURNED,
        user,
        case,
        {"file": str(file.id), "reason": body.reason.value},
    )
    await session.commit()
    await session.refresh(file)
    return file_out(file, None)


@router.delete("/cases/{case_id}/package-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    case_id: uuid.UUID, file_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """The specialist deletes any file; an employee only one they added."""
    case = await case_of(session, user, case_id)
    file = await _file(session, case, file_id)
    if user.role != UserRole.SPECIALIST and file.uploaded_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    await session.delete(file)
    audit_case(session, AuditAction.PORTAL_FILE_DELETED, user, case, {"file": str(file.id)})
    await session.commit()
    await remove_objects([object_key(case.tenant_id, file.id)], "deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

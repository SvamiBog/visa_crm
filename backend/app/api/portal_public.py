"""The client's side of the portal (backlog 2.9): public addresses under /api/portal/<token>.

Nobody here is signed in to the CRM. A request first finds the link by the hash of its
token in the system scope; everything after that runs in the scope of that link's
workspace and touches only that link's case. The link, the case still open, the workspace
active with the «package_check» module: otherwise «link_invalid», the same for a link that
never was. Uploads and the page need the client's session (its own cookie), which a code
opens; a wrong, blocked or replaced code all get «code_invalid».

Failures from one address are limited (codes asked for, codes tried, unknown links), the
body of a request is limited before it is read (app/core/request_limit.py), and no file is
ever given back: the client sees names and statuses only.
"""

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import Base64Bytes, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Scope

from app.api.deps import _cookie_values
from app.api.package import checklist_item, checklist_of, files_of, marks_of
from app.api.package_files import checked_type, put_file, remove_objects
from app.api.portal import audit_case, issue_code
from app.core import portal
from app.core.audit import client_ip
from app.core.config import get_settings
from app.core.db import get_sessionmaker, scope_to_system, scope_to_tenant
from app.core.documents import clean_file_name, object_key
from app.core.portal_notice import Controller, language_of, render
from app.core.security import ClientFailures
from app.core.vault import get_tenant_key
from app.models import (
    AuditAction,
    ClientLink,
    ClientSession,
    CodeChannel,
    ConsentGivenBy,
    Contact,
    Country,
    FileSource,
    FileStatus,
    OwnChecklistItem,
    PackageFile,
    PortalConsent,
    PortalNotice,
    ReturnReason,
    Tenant,
    VisaCase,
)

router = APIRouter(prefix="/portal", tags=["portal"])

PORTAL_COOKIE = "visacrm_portal"
HOST_PORTAL_COOKIE = f"__Host-{PORTAL_COOKIE}"
# Failures from one address: a family behind one router asks and mistypes a few times;
# a script trying links or codes stops quickly.
MAX_CLIENT_FAILURES = 20
CLIENT_WINDOW = timedelta(minutes=15)
NEEDED_MODULES = ("clients", "cases", "package_check")


def cookie_name() -> str:
    return HOST_PORTAL_COOKIE if get_settings().cookie_secure else PORTAL_COOKIE


def _session_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _failures(request: Request) -> ClientFailures:
    """Failures of each address, kept with the app (a new app in each test starts clean)."""
    failures = getattr(request.app.state, "portal_failures", None)
    if failures is None:
        failures = ClientFailures(MAX_CLIENT_FAILURES, CLIENT_WINDOW.total_seconds())
        request.app.state.portal_failures = failures
    return failures


def _take_try(request: Request) -> float:
    mark = _failures(request).take(client_ip.get() or "")
    if mark is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")
    return mark


def _give_back(request: Request, mark: float) -> None:
    _failures(request).give_back(client_ip.get() or "", mark)


@dataclass(frozen=True)
class Found:
    link: ClientLink
    case: VisaCase


async def _find_link(system: AsyncSession, token: str, *, lock: bool = False) -> Found | None:
    """The link of the token if it still works: not revoked, its case open, its workspace
    active with the module. System scope: the workspace is not known yet."""
    if not portal.is_link_token(token):
        return None
    query = (
        select(ClientLink, VisaCase, Tenant)
        .join(VisaCase, VisaCase.id == ClientLink.case_id)
        .join(Tenant, Tenant.id == ClientLink.tenant_id)
        .where(ClientLink.token_hash == portal.link_token_hash(token))
    )
    if lock:
        query = query.with_for_update(of=ClientLink)
    row = (await system.execute(query)).first()
    if row is None:
        return None
    link, case, tenant = row
    if not portal.link_open(case.stage, case.outcome, link.revoked_at):
        return None
    if not tenant.is_active or not all(m in tenant.modules for m in NEEDED_MODULES):
        return None
    return Found(link, case)


def _link_invalid() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "link_invalid")


async def _system() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        scope_to_system(session)
        yield session


System = Annotated[AsyncSession, Depends(_system)]


@dataclass(frozen=True)
class Visitor:
    """A client signed in to one link, with a session of that link's workspace."""

    link: ClientLink
    case: VisaCase
    session_row_id: uuid.UUID
    code_channel: CodeChannel
    session: AsyncSession


async def visitor(token: str, request: Request) -> AsyncIterator[Visitor]:
    """The client signed in to this link; 404 for a link that does not work, 401 without a
    live session of it. Each request moves the session's last action on."""
    async with get_sessionmaker()() as system:
        scope_to_system(system)
        found = await _find_link(system, token)
        if found is None:
            raise _link_invalid()
        values = _cookie_values(request, cookie_name())
        if len(values) != 1:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
        row = await system.scalar(
            select(ClientSession).where(
                ClientSession.token_hash == _session_hash(values[0]),
                ClientSession.link_id == found.link.id,
            )
        )
        now = datetime.now(UTC)
        if row is None or not portal.session_alive(row.created_at, row.last_seen_at, now):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
        row.last_seen_at = now
        await system.commit()
        session_id, channel = row.id, row.code_channel
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, found.case.tenant_id)
        case = await session.get_one(VisaCase, found.case.id)
        link = await session.get_one(ClientLink, found.link.id)
        yield Visitor(link, case, session_id, channel, session)


Client = Annotated[Visitor, Depends(visitor)]


async def portal_signed_in(scope: Scope) -> bool:
    """For the body limit (app/core/request_limit.py): the request comes with a live
    session of the link in its path, as `visitor` sees it, without moving it on."""
    request = Request(scope)
    token = scope["path"].split("/")[3]
    values = _cookie_values(request, cookie_name())
    if len(values) != 1:
        return False
    async with get_sessionmaker()() as system:
        scope_to_system(system)
        found = await _find_link(system, token)
        if found is None:
            return False
        row = await system.scalar(
            select(ClientSession).where(
                ClientSession.token_hash == _session_hash(values[0]),
                ClientSession.link_id == found.link.id,
            )
        )
        return row is not None and portal.session_alive(
            row.created_at, row.last_seen_at, datetime.now(UTC)
        )


# --- Schemas -------------------------------------------------------------------------


class CodeAsked(BaseModel):
    # email: sent to the address of the card; specialist: ask the specialist for it.
    channel: CodeChannel


class LoginIn(BaseModel):
    code: Annotated[str, Field(min_length=1, max_length=12)]


class CountryOut(BaseModel):
    code: str
    name_ru: str
    name_en: str


class ClientFileOut(BaseModel):
    id: uuid.UUID
    file_name: str
    status: FileStatus
    return_reason: ReturnReason | None
    return_note: str | None
    created_at: datetime
    # The client's own file still waiting for review.
    deletable: bool


class ClientItemOut(BaseModel):
    id: uuid.UUID
    name: str
    note: str | None
    required: bool
    status: portal.ClientItemStatus
    files: list[ClientFileOut]


class NoticeOut(BaseModel):
    id: uuid.UUID
    version: str
    language: str
    body: str


class ConsentState(BaseModel):
    needed: bool
    # None until the specialist has filled in her details as the data controller.
    notice: NoticeOut | None
    # Where to write to withdraw the consent or ask to delete the data.
    withdraw_email: str | None


class PageOut(BaseModel):
    country: CountryOut
    # The specialist's details are filled in: uploads can start after the consent.
    ready: bool
    consent: ConsentState
    items: list[ClientItemOut]


class ConsentIn(BaseModel):
    notice_id: uuid.UUID
    special_categories: bool
    given_by: ConsentGivenBy
    representative_name: Annotated[str, Field(max_length=200)] | None = None

    @model_validator(mode="after")
    def representative_named(self) -> "ConsentIn":
        self.representative_name = (self.representative_name or "").strip() or None
        if (self.given_by == ConsentGivenBy.REPRESENTATIVE) != bool(self.representative_name):
            raise ValueError("representative_name_required")
        return self


class UploadIn(BaseModel):
    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Base64Bytes


# --- Sign-in -------------------------------------------------------------------------


@router.post("/{token}/code")
async def ask_code(token: str, request: Request, system: System) -> CodeAsked:
    """The client asks for a code: it goes to the email of the card; without one, the
    client is told to ask the specialist (who sees «Новый код» in the case)."""
    mark = _take_try(request)
    found = await _find_link(system, token)
    if found is None:
        raise _link_invalid()
    async with get_sessionmaker()() as session:
        scope_to_tenant(session, found.case.tenant_id)
        contact = await session.get_one(Contact, found.case.contact_id)
        if not contact.email:
            _give_back(request, mark)
            return CodeAsked(channel=CodeChannel.SPECIALIST)
        link = await session.get_one(ClientLink, found.link.id)
        case = await session.get_one(VisaCase, found.case.id)
        # The link's code, the same as before: a new one would lock out a code the
        # specialist has already passed on.
        await issue_code(
            session, system, link, case, CodeChannel.EMAIL, None, contact.email, new=False
        )
    _give_back(request, mark)
    return CodeAsked(channel=CodeChannel.EMAIL)


@router.post("/{token}/login")
async def login(
    token: str, body: LoginIn, request: Request, response: Response, system: System
) -> dict[str, bool]:
    mark = _take_try(request)
    found = await _find_link(system, token, lock=True)
    if found is None:
        raise _link_invalid()
    link = found.link
    # The try counts before the code is compared, under the row lock: tries sent at once
    # take turns and cannot outrun the five.
    attempts = link.code_attempts
    # Capped: tries past the limit count no further (the column is small).
    link.code_attempts = min(attempts + 1, portal.MAX_CODE_ATTEMPTS)
    matches = portal.code_matches(link.id, body.code, stored_hash=link.code_hash, attempts=attempts)
    if not matches:
        await system.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code_invalid")
    channel = link.code_channel or CodeChannel.SPECIALIST
    # The code stays for the next sign-in; the wrong tries before this one are forgiven.
    link.code_attempts = 0
    session_token = secrets.token_urlsafe(32)
    system.add(
        ClientSession(
            tenant_id=link.tenant_id,
            link_id=link.id,
            case_id=link.case_id,
            token_hash=_session_hash(session_token),
            code_channel=channel,
        )
    )
    audit_case(
        system,
        AuditAction.PORTAL_SIGNED_IN,
        None,
        found.case,
        {"link": str(link.id), "channel": channel.value},
    )
    await system.commit()
    _give_back(request, mark)
    response.set_cookie(
        cookie_name(),
        session_token,
        max_age=int(portal.SESSION_MAX.total_seconds()),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/{token}/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(client: Client, response: Response) -> Response:
    row = await client.session.get(ClientSession, client.session_row_id)
    if row is not None:
        await client.session.delete(row)
        await client.session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(cookie_name(), path="/", secure=get_settings().cookie_secure)
    return response


# --- The page ------------------------------------------------------------------------


async def _notice(session: AsyncSession, case: VisaCase, language: str) -> PortalNotice | None:
    """The notice as this workspace shows it now, kept the first time it is shown."""
    tenant = await session.get_one(Tenant, case.tenant_id)
    controller = Controller(
        tenant.controller_name,
        tenant.controller_address,
        tenant.controller_email,
        tenant.supervisory_authority,
    )
    if not controller.ready:
        return None
    notice = render(controller, language)
    kept = await session.scalar(
        select(PortalNotice).where(
            PortalNotice.tenant_id == case.tenant_id, PortalNotice.sha256 == notice.sha256
        )
    )
    if kept is None:
        # Two pages opened at once both keep it: the second finds the first's row.
        await session.execute(
            insert(PortalNotice)
            .values(
                id=uuid.uuid4(),
                tenant_id=case.tenant_id,
                version=notice.version,
                language=notice.language,
                body=notice.body,
                sha256=notice.sha256,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "sha256"])
        )
        kept = await session.scalar(
            select(PortalNotice).where(
                PortalNotice.tenant_id == case.tenant_id, PortalNotice.sha256 == notice.sha256
            )
        )
    return kept


async def _consented(session: AsyncSession, case: VisaCase) -> bool:
    return (
        await session.scalar(select(PortalConsent.id).where(PortalConsent.case_id == case.id))
    ) is not None


@router.get("/{token}")
async def page(client: Client, lang: str | None = None) -> PageOut:
    session, case = client.session, client.case
    country = await session.get_one(Country, case.destination_id)
    tenant = await session.get_one(Tenant, case.tenant_id)
    notice = await _notice(session, case, language_of(lang))
    consented = await _consented(session, case)
    marks = await marks_of(session, case)
    files = await files_of(session, case.id)
    items = []
    for item in await checklist_of(session, case):
        mark = marks.get(item.id)
        item_files = files.get(item.id, [])
        shown = portal.client_status(mark.state if mark else None, [f.status for f in item_files])
        if shown == portal.ClientItemStatus.NOT_NEEDED:
            continue
        items.append(
            ClientItemOut(
                id=item.id,
                name=item.name,
                note=item.note,
                required=item.required,
                status=shown,
                files=[
                    ClientFileOut(
                        id=f.id,
                        file_name=f.file_name,
                        status=f.status,
                        return_reason=f.return_reason,
                        return_note=f.return_note,
                        created_at=f.created_at,
                        deletable=f.source == FileSource.CLIENT and f.status == FileStatus.PENDING,
                    )
                    for f in item_files
                ],
            )
        )
    await session.commit()
    return PageOut(
        country=CountryOut(code=country.code, name_ru=country.name_ru, name_en=country.name_en),
        ready=notice is not None,
        consent=ConsentState(
            needed=not consented,
            notice=NoticeOut(
                id=notice.id, version=notice.version, language=notice.language, body=notice.body
            )
            if notice
            else None,
            withdraw_email=tenant.controller_email,
        ),
        items=items,
    )


@router.post("/{token}/consent", status_code=status.HTTP_201_CREATED)
async def give_consent(client: Client, body: ConsentIn, request: Request) -> dict[str, bool]:
    """The notice acknowledged and, apart from it, the consent to special categories of
    data, kept with how and when it was given: the specialist's proof."""
    session, case = client.session, client.case
    notice = await session.get(PortalNotice, body.notice_id)
    if notice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    given = await session.scalar(
        select(PortalConsent.id).where(
            PortalConsent.case_id == case.id, PortalConsent.notice_id == notice.id
        )
    )
    if given is not None:
        # A repeated press of «Продолжить» (or a script) adds no rows and no events.
        return {"ok": True}
    session.add(
        PortalConsent(
            tenant_id=case.tenant_id,
            contact_id=case.contact_id,
            case_id=case.id,
            link_id=client.link.id,
            notice_id=notice.id,
            special_categories=body.special_categories,
            given_by=body.given_by,
            representative_name=body.representative_name,
            code_channel=client.code_channel,
            ip=client_ip.get(),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    audit_case(
        session,
        AuditAction.PORTAL_CONSENT_GIVEN,
        None,
        case,
        {
            "link": str(client.link.id),
            "notice": str(notice.id),
            "special_categories": str(body.special_categories).lower(),
        },
    )
    await session.commit()
    return {"ok": True}


@router.post("/{token}/items/{item_id}/files", status_code=status.HTTP_201_CREATED)
async def upload(
    client: Client, item_id: uuid.UUID, body: UploadIn, system: System
) -> ClientFileOut:
    session, case = client.session, client.case
    item = await checklist_item(session, case, item_id)
    if await _notice(session, case, "ru") is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "portal_not_ready")
    if not await _consented(session, case):
        raise HTTPException(status.HTTP_409_CONFLICT, "consent_required")
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
        source=FileSource.CLIENT,
        status=FileStatus.PENDING,
        link_id=client.link.id,
    )
    key = await get_tenant_key(session, case.tenant_id)
    await session.commit()
    await put_file(session, system, file, key, body.content)
    audit_case(
        session,
        AuditAction.PORTAL_FILE_UPLOADED,
        None,
        case,
        {"link": str(client.link.id), "file": str(file.id)},
    )
    await session.commit()
    await session.refresh(file)
    return ClientFileOut(
        id=file.id,
        file_name=file.file_name,
        status=file.status,
        return_reason=None,
        return_note=None,
        created_at=file.created_at,
        deletable=True,
    )


@router.delete("/{token}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_own_file(client: Client, file_id: uuid.UUID) -> Response:
    """Only a file the client uploaded and the specialist has not reviewed yet."""
    session, case = client.session, client.case
    file = await session.get(PackageFile, file_id)
    if file is None or file.case_id != case.id or file.source != FileSource.CLIENT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if file.status != FileStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "file_reviewed")
    await session.delete(file)
    audit_case(
        session,
        AuditAction.PORTAL_FILE_DELETED,
        None,
        case,
        {"link": str(client.link.id), "file": str(file.id)},
    )
    await session.commit()
    await remove_objects([object_key(case.tenant_id, file.id)], "deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

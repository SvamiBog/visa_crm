"""Document templates (backlog 2.1): a specialist's own Word files with codes, and the
templates given to a client. Closed by the «document_templates» module.

The specialist uploads, changes and deletes templates; everyone with the module gives
them to clients and downloads them filled. The filled file is made on every download
from the data of that moment and never stored; each download is written to the audit log,
as it carries the client's data. The blank template is encrypted with the workspace's key
before it goes to the storage, like client files (app/api/documents.py).
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Base64Bytes, BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    TenantSession,
    effective_modules,
    require_module,
    require_specialist,
    workspace_today,
)
from app.core.audit import record
from app.core.documents import content_disposition
from app.core.docx_fill import CODE, DocxError, codes_in, fill, read_docx
from app.core.questionnaire import Questionnaire
from app.core.storage import (
    StorageFailed,
    StorageNotConfigured,
    delete_object,
    get_object,
    is_configured,
    put_object,
)
from app.core.templates import (
    CODES,
    DOCX,
    SEX,
    Localized,
    Money,
    clean_source_name,
    file_name,
    filler,
    missing,
    object_key,
    open_template,
    resolve,
    seal_template,
    split_codes,
)
from app.core.vault import get_tenant_key
from app.models import (
    AuditAction,
    CaseFinance,
    Contact,
    ContactTemplate,
    Country,
    DocumentTemplate,
    Tenant,
    VisaCase,
    VisaPurpose,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/templates",
    tags=["templates"],
    dependencies=[Depends(require_module("document_templates"))],
)
contact_router = APIRouter(
    prefix="/contacts",
    tags=["contacts"],
    dependencies=[
        Depends(require_module("clients")),
        Depends(require_module("document_templates")),
    ],
)

Specialist = Annotated[CurrentUser, Depends(require_specialist)]
Member = Annotated[CurrentUser, Depends(require_module("document_templates"))]

# Per workspace: letters, statements, powers of attorney, contracts — a few dozen at most.
MAX_TEMPLATES = 200
_TELEGRAM_HANDLE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


# --- Schemas -------------------------------------------------------------------------


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


Name = Annotated[str, Field(min_length=1, max_length=200)]
Pattern = Annotated[str, Field(max_length=200)]


class TemplateIn(BaseModel):
    # The file's name without .docx when not given.
    name: Name | None = None
    file_name: Pattern | None = None
    source_name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Base64Bytes

    _name = field_validator("name", mode="before")(_blank_is_none)
    _file_name = field_validator("file_name", mode="before")(_blank_is_none)


class TemplateUpdate(BaseModel):
    """Only the fields sent are changed; file_name null — the default name."""

    name: Name | None = None
    file_name: Pattern | None = None
    is_active: bool | None = None

    _name = field_validator("name", mode="before")(_strip)
    _file_name = field_validator("file_name", mode="before")(_blank_is_none)


class FileIn(BaseModel):
    source_name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Base64Bytes


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    file_name: str | None
    source_name: str
    size_bytes: int
    # Keys of the known codes found in the file, in the order they come.
    codes: list[str]
    # Codes the file has that do not exist, as written: typos to fix.
    unknown_codes: list[str]
    is_active: bool
    # How many clients have it.
    clients: int
    created_at: datetime
    updated_at: datetime


class CodeOut(BaseModel):
    key: str
    group: str
    ru: str
    en: str


class GiveIn(BaseModel):
    template_id: uuid.UUID
    # One of the client's cases: the codes of the case come from it.
    case_id: uuid.UUID | None = None


class ContactTemplateOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    case_id: uuid.UUID | None
    # What the filled file will be called.
    file_name: str
    # Codes of the template the client has no value for yet.
    missing: list[str]
    unknown_codes: list[str]
    template_active: bool
    created_at: datetime


# --- Helpers -------------------------------------------------------------------------


def _storage_error(error: Exception) -> HTTPException:
    if isinstance(error, StorageNotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed")


def _read(content: bytes) -> tuple[list[str], list[str]]:
    """The file's known and unknown codes; 413/422 if it is not a Word file to fill."""
    try:
        return split_codes(codes_in(read_docx(content)))
    except DocxError as error:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code == "template_too_large"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, error.code) from error


def _check_pattern(pattern: str | None) -> None:
    """A file name may hold only codes that exist: a typo would vanish from it silently."""
    for match in CODE.finditer(pattern or ""):
        if resolve(match.group(1)) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_code_in_name")


def _audit(
    session: AsyncSession, action: AuditAction, user: CurrentUser, template: DocumentTemplate
) -> None:
    """A template's name is the specialist's, not a client's."""
    record(
        session,
        action,
        actor=user,
        target_type="document_template",
        target_id=template.id,
        details={"name": template.name},
    )


def _audit_given(
    session: AsyncSession, action: AuditAction, user: CurrentUser, given: ContactTemplate
) -> None:
    """Ids only: the file name carries the client's name."""
    details: dict[str, Any] = {"template": str(given.template_id)}
    if given.case_id is not None:
        details["case"] = str(given.case_id)
    record(
        session,
        action,
        actor=user,
        target_type="contact",
        target_id=given.contact_id,
        details=details,
    )


async def _template(session: AsyncSession, template_id: uuid.UUID) -> DocumentTemplate:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    template = await session.get(DocumentTemplate, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return template


async def _clients(session: AsyncSession, template_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(ContactTemplate.template_id, func.count(func.distinct(ContactTemplate.contact_id)))
        .where(ContactTemplate.template_id.in_(template_ids))
        .group_by(ContactTemplate.template_id)
    )
    return {template_id: count for template_id, count in rows}


def _out(template: DocumentTemplate, clients: int) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        file_name=template.file_name,
        source_name=template.source_name,
        size_bytes=template.size_bytes,
        codes=template.codes,
        unknown_codes=template.unknown_codes,
        is_active=template.is_active,
        clients=clients,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def _store(
    session: AsyncSession, user: CurrentUser, template_id: uuid.UUID, content: bytes
) -> None:
    assert user.tenant_id is not None
    key = await get_tenant_key(session, user.tenant_id)
    # The workspace row stays locked until commit: release it (keeping a key made on first
    # use) before the upload, which may take seconds.
    await session.commit()
    try:
        await put_object(
            object_key(user.tenant_id, template_id),
            seal_template(key, user.tenant_id, template_id, content),
        )
    except (StorageNotConfigured, StorageFailed) as error:
        raise _storage_error(error) from error


async def _load(session: AsyncSession, user: CurrentUser, template: DocumentTemplate) -> bytes:
    """The blank template, decrypted."""
    assert user.tenant_id is not None
    try:
        blob = await get_object(object_key(user.tenant_id, template.id))
    except (StorageNotConfigured, StorageFailed) as error:
        raise _storage_error(error) from error
    key = await get_tenant_key(session, user.tenant_id)
    try:
        return open_template(key, user.tenant_id, template.id, blob)
    except InvalidTag as error:
        logger.error("Template %s does not decrypt", template.id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed") from error


async def _country(session: AsyncSession, country_id: uuid.UUID | None) -> Localized | None:
    country = await session.get(Country, country_id) if country_id is not None else None
    return Localized(country.name_ru, country.name_en) if country is not None else None


async def client_data(
    session: AsyncSession, user: CurrentUser, contact: Contact, case: VisaCase | None
) -> dict[str, object]:
    """What the codes insert for this client (app/core/templates.py → CODES). A case's
    codes need the «cases» module, its sums the «finances» one: a template does not show
    the user more than the card does."""
    assert user.tenant_id is not None
    questionnaire = Questionnaire.model_validate(contact.questionnaire)
    nationality: Localized | str | None = questionnaire.nationality
    if nationality is not None:
        country = await session.scalar(select(Country).where(Country.code == nationality))
        if country is not None:
            nationality = Localized(country.name_ru, country.name_en)
    telegram = contact.telegram
    if telegram and _TELEGRAM_HANDLE.match(telegram):
        telegram = f"@{telegram}"
    data: dict[str, object] = {
        "full_name": contact.full_name,
        "last_name": contact.last_name,
        "first_name": contact.first_name,
        "middle_name": contact.middle_name,
        "full_name_latin": contact.full_name_latin,
        "last_name_latin": contact.last_name_latin,
        "first_name_latin": contact.first_name_latin,
        "birth_date": contact.birth_date,
        "phone": contact.phone,
        "email": contact.email,
        "telegram": telegram,
        "contract_number": contact.contract_number,
        "contract_date": contact.contract_signed_on,
        "passport_number": questionnaire.passport_number,
        "passport_issued_on": questionnaire.passport_issued_on,
        "passport_expires_on": questionnaire.passport_expires_on,
        "passport_issued_by": questionnaire.passport_issued_by,
        "nationality": nationality,
        "sex": SEX.get(questionnaire.sex) if questionnaire.sex else None,
        "personal_number": questionnaire.personal_number,
        "birth_place": questionnaire.birth_place,
        "today": await workspace_today(session, user.tenant_id),
    }
    modules = await effective_modules(session, user)
    if case is None or "cases" not in modules:
        return data
    purpose = await session.get(VisaPurpose, case.purpose_id) if case.purpose_id else None
    data.update(
        case_number=case.number,
        destination=await _country(session, case.destination_id),
        submission_country=await _country(session, case.submission_country_id),
        purpose=purpose.name if purpose is not None else None,
        submission_date=case.submission_on,
        vac_number=case.vac_number,
    )
    if "finances" in modules:
        finance = await session.get(CaseFinance, case.id)
        currency = await session.scalar(
            select(Tenant.base_currency).where(Tenant.id == user.tenant_id)
        )
        if finance is not None and currency is not None:
            for field in ("price", "prepayment"):
                amount = getattr(finance, field)
                if amount is not None:
                    data[field] = Money(amount, currency)
    return data


# --- Templates -----------------------------------------------------------------------


@router.get("/codes")
async def list_codes() -> list[CodeOut]:
    """Every code, for the instruction: how it is written in Russian and in English."""
    return [CodeOut(key=c.key, group=c.group, ru=c.ru[0], en=c.en[0]) for c in CODES]


@router.get("")
async def list_templates(session: TenantSession) -> list[TemplateOut]:
    """The workspace's templates by name, those in use first."""
    templates = list(await session.scalars(select(DocumentTemplate)))
    templates.sort(key=lambda t: (not t.is_active, t.name.casefold()))
    clients = await _clients(session, [t.id for t in templates])
    return [_out(t, clients.get(t.id, 0)) for t in templates]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_template(
    body: TemplateIn, user: Specialist, session: TenantSession
) -> TemplateOut:
    assert user.tenant_id is not None
    if not is_configured():
        raise _storage_error(StorageNotConfigured())
    _check_pattern(body.file_name)
    codes, unknown = _read(body.content)
    count = await session.scalar(select(func.count()).select_from(DocumentTemplate))
    if (count or 0) >= MAX_TEMPLATES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_templates")
    source_name = clean_source_name(body.source_name)
    template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        name=body.name or source_name.removesuffix(".docx"),
        file_name=body.file_name,
        source_name=source_name,
        size_bytes=len(body.content),
        codes=codes,
        unknown_codes=unknown,
        uploaded_by=user.id,
    )
    await _store(session, user, template.id, body.content)
    session.add(template)
    await session.flush()
    _audit(session, AuditAction.TEMPLATE_UPLOADED, user, template)
    await session.commit()
    await session.refresh(template)
    return _out(template, 0)


@router.patch("/{template_id}")
async def update_template(
    template_id: uuid.UUID, body: TemplateUpdate, user: Specialist, session: TenantSession
) -> TemplateOut:
    template = await _template(session, template_id)
    _check_pattern(body.file_name)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "file_name":
            setattr(template, field, value)
    _audit(session, AuditAction.TEMPLATE_UPDATED, user, template)
    await session.commit()
    await session.refresh(template)
    return _out(template, (await _clients(session, [template.id])).get(template.id, 0))


@router.put("/{template_id}/file")
async def replace_file(
    template_id: uuid.UUID, body: FileIn, user: Specialist, session: TenantSession
) -> TemplateOut:
    """A new version of the file: the clients who have the template get it from now on."""
    template = await _template(session, template_id)
    codes, unknown = _read(body.content)
    await _store(session, user, template.id, body.content)
    template = await _template(session, template_id)
    template.source_name = clean_source_name(body.source_name)
    template.size_bytes = len(body.content)
    template.codes = codes
    template.unknown_codes = unknown
    _audit(session, AuditAction.TEMPLATE_UPDATED, user, template)
    await session.commit()
    await session.refresh(template)
    return _out(template, (await _clients(session, [template.id])).get(template.id, 0))


@router.get("/{template_id}/file")
async def download_template(
    template_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """The blank template as it was uploaded: no client data, so not in the log."""
    template = await _template(session, template_id)
    content = await _load(session, user, template)
    return Response(
        content=content,
        media_type=DOCX,
        headers={"Content-Disposition": content_disposition(template.source_name)},
    )


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID, user: Specialist, session: TenantSession
) -> Response:
    """Goes from the clients who have it too; their data stays in their cards."""
    assert user.tenant_id is not None
    template = await _template(session, template_id)
    await session.delete(template)
    _audit(session, AuditAction.TEMPLATE_DELETED, user, template)
    await session.commit()
    try:
        await delete_object(object_key(user.tenant_id, template.id))
    except (StorageNotConfigured, StorageFailed):
        # The row is gone; what is left in the storage is ciphertext nobody refers to.
        logger.warning("Object of deleted template %s was not removed", template.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- A client's templates ------------------------------------------------------------


async def _contact(session: AsyncSession, contact_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return contact


async def _case(
    session: AsyncSession, contact_id: uuid.UUID, case_id: uuid.UUID | None
) -> VisaCase | None:
    """One of this client's cases."""
    if case_id is None:
        return None
    case = await session.get(VisaCase, case_id)
    if case is None or case.contact_id != contact_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return case


async def _case_of(
    session: AsyncSession, contact_id: uuid.UUID, given: ContactTemplate
) -> VisaCase | None:
    """The case a given template is for, if it is still one of the client's."""
    case = await session.get(VisaCase, given.case_id) if given.case_id is not None else None
    return case if case is not None and case.contact_id == contact_id else None


async def _given(
    session: AsyncSession, contact_id: uuid.UUID, given_id: uuid.UUID
) -> ContactTemplate:
    given = await session.get(ContactTemplate, given_id)
    if given is None or given.contact_id != contact_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return given


@contact_router.get("/{contact_id}/templates")
async def list_given(
    contact_id: uuid.UUID, user: Member, session: TenantSession
) -> list[ContactTemplateOut]:
    """The client's templates, newest first, with what their files will be called and
    which of their codes have no value yet."""
    contact = await _contact(session, contact_id)
    rows = await session.execute(
        select(ContactTemplate, DocumentTemplate)
        .join(DocumentTemplate, DocumentTemplate.id == ContactTemplate.template_id)
        .where(ContactTemplate.contact_id == contact_id)
        .order_by(ContactTemplate.created_at.desc(), ContactTemplate.id.desc())
    )
    data: dict[uuid.UUID | None, dict[str, object]] = {}
    result = []
    for given, template in rows:
        if given.case_id not in data:
            case = await _case_of(session, contact_id, given)
            data[given.case_id] = await client_data(session, user, contact, case)
        values = data[given.case_id]
        result.append(
            ContactTemplateOut(
                id=given.id,
                template_id=template.id,
                name=template.name,
                case_id=given.case_id,
                file_name=file_name(template.file_name, template.name, values),
                missing=missing(template.codes, values),
                unknown_codes=template.unknown_codes,
                template_active=template.is_active,
                created_at=given.created_at,
            )
        )
    return result


@contact_router.post("/{contact_id}/templates", status_code=status.HTTP_201_CREATED)
async def give_template(
    contact_id: uuid.UUID, body: GiveIn, user: Member, session: TenantSession
) -> dict[str, uuid.UUID]:
    assert user.tenant_id is not None
    await _contact(session, contact_id)
    template = await _template(session, body.template_id)
    if not template.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "template_switched_off")
    await _case(session, contact_id, body.case_id)
    taken = await session.scalar(
        select(ContactTemplate.id).where(
            ContactTemplate.contact_id == contact_id,
            ContactTemplate.template_id == template.id,
            ContactTemplate.case_id.is_(None)
            if body.case_id is None
            else ContactTemplate.case_id == body.case_id,
        )
    )
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "template_already_given")
    given = ContactTemplate(
        tenant_id=user.tenant_id,
        contact_id=contact_id,
        template_id=template.id,
        case_id=body.case_id,
        created_by=user.id,
    )
    session.add(given)
    await session.flush()
    _audit_given(session, AuditAction.CONTACT_TEMPLATE_ADDED, user, given)
    await session.commit()
    return {"id": given.id}


@contact_router.delete("/{contact_id}/templates/{given_id}", status_code=status.HTTP_204_NO_CONTENT)
async def take_back_template(
    contact_id: uuid.UUID, given_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    given = await _given(session, contact_id, given_id)
    await session.delete(given)
    _audit_given(session, AuditAction.CONTACT_TEMPLATE_REMOVED, user, given)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@contact_router.get("/{contact_id}/templates/{given_id}/file")
async def download_filled(
    contact_id: uuid.UUID, given_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    """The template filled with the client's data of this moment, named for the client.
    Always a download; every call is written to the audit log."""
    contact = await _contact(session, contact_id)
    given = await _given(session, contact_id, given_id)
    template = await _template(session, given.template_id)
    values = await client_data(session, user, contact, await _case_of(session, contact_id, given))
    blank = await _load(session, user, template)
    try:
        content = fill(read_docx(blank), filler(values))
    except DocxError as error:
        # Checked when it was uploaded: a stored file that no longer reads is damage.
        logger.error("Template %s does not read: %s", template.id, error.code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "storage_failed") from error
    _audit_given(session, AuditAction.CONTACT_TEMPLATE_OPENED, user, given)
    await session.commit()
    return Response(
        content=content,
        media_type=DOCX,
        headers={
            "Content-Disposition": content_disposition(
                file_name(template.file_name, template.name, values)
            )
        },
    )

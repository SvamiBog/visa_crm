"""Contacts (backlog 1.2): the card, search, the duplicate guard by phone, the notes
timeline and links between contacts. Closed by the «clients» module.

Personal data stays out of URLs and logs: search takes its query in the request body,
and audit events carry ids and the names of changed fields only.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cases import CaseOut, cases_of
from app.api.consultations import ConsultationOut, HintOut, consultations_of, hints_for
from app.api.deps import CurrentUser, TenantSession, effective_modules, require_module
from app.core.audit import record
from app.core.phone import normalize_phone, phone_digits
from app.core.questionnaire import Questionnaire
from app.models import (
    AuditAction,
    Contact,
    ContactNote,
    ContactRelation,
    LeadChannel,
    Messenger,
    Partner,
    User,
    UserRole,
)

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(require_module("clients"))]
)

Member = Annotated[CurrentUser, Depends(require_module("clients"))]

EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
SEARCH_LIMIT = 50


# --- Schemas -------------------------------------------------------------------------


class ContactFields(BaseModel):
    full_name_latin: Annotated[str, Field(max_length=200)] | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    telegram: Annotated[str, Field(max_length=65)] | None = None
    messenger: Messenger | None = None
    messenger_contact: Annotated[str, Field(max_length=64)] | None = None
    email: Annotated[str, Field(max_length=320, pattern=EMAIL)] | None = None
    birth_date: date | None = None
    lead_channel_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    consent_signed_on: date | None = None
    contract_number: Annotated[str, Field(max_length=50)] | None = None
    contract_signed_on: date | None = None

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_phone(value)

    @field_validator("telegram")
    @classmethod
    def telegram_without_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().removeprefix("@").strip()
        return value or None

    @field_validator("full_name_latin", "messenger_contact", "email", "contract_number")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ContactIn(ContactFields):
    full_name: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("full_name")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value


class ContactUpdate(ContactFields):
    """Only the fields sent are changed; null clears an optional field."""

    full_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    full_name_latin: str | None
    phone: str | None
    telegram: str | None
    messenger: Messenger | None
    messenger_contact: str | None
    email: str | None
    birth_date: date | None
    lead_channel_id: uuid.UUID | None
    partner_id: uuid.UUID | None
    consent_signed_on: date | None
    contract_number: str | None
    contract_signed_on: date | None
    created_at: datetime
    updated_at: datetime


class ContactListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    full_name_latin: str | None
    phone: str | None
    telegram: str | None
    lead_channel_id: uuid.UUID | None
    created_at: datetime


class SearchIn(BaseModel):
    query: Annotated[str, Field(max_length=100)] = ""
    offset: int = Field(default=0, ge=0)


class SearchOut(BaseModel):
    items: list[ContactListItem]
    has_more: bool


class NoteIn(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=5000)]

    @field_validator("body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a note cannot be empty")
        return value


class NoteOut(BaseModel):
    id: uuid.UUID
    body: str
    author_id: uuid.UUID | None
    author_email: str | None
    created_at: datetime
    edited_at: datetime | None


class RelationIn(BaseModel):
    related_id: uuid.UUID
    # How the related contact is linked to this one ("муж"); reverse_label is the other way.
    label: Annotated[str, Field(max_length=100)] | None = None
    reverse_label: Annotated[str, Field(max_length=100)] | None = None


class RelationOut(BaseModel):
    related_id: uuid.UUID
    full_name: str
    label: str | None


class ContactCard(BaseModel):
    contact: ContactOut
    notes: list[NoteOut]
    relations: list[RelationOut]
    consultations: list[ConsultationOut]
    questionnaire: Questionnaire
    # Questionnaire checks against the latest consultation and the cases in work
    # (app/core/consultation_checks.py).
    hints: list[HintOut]
    # None when the user has no «cases» module.
    cases: list[CaseOut] | None


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    contact_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    """Ids only: no names, phones or note text in the log (CLAUDE.md)."""
    record(
        session, action, actor=user, target_type="contact", target_id=contact_id, details=details
    )


async def _contact(session: AsyncSession, contact_id: uuid.UUID) -> Contact:
    """RLS hides other workspaces' rows, so their ids are simply not found."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return contact


async def _check_refs(session: AsyncSession, fields: dict[str, Any]) -> None:
    for field, model in (("lead_channel_id", LeadChannel), ("partner_id", Partner)):
        if fields.get(field) is not None and await session.get(model, fields[field]) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def _phone_owner(
    session: AsyncSession, phone: str | None, exclude: uuid.UUID | None = None
) -> uuid.UUID | None:
    if phone is None:
        return None
    query = select(Contact.id).where(Contact.phone == phone)
    if exclude is not None:
        query = query.where(Contact.id != exclude)
    return await session.scalar(query)


def _phone_taken(contact_id: uuid.UUID) -> JSONResponse:
    """409 that points to the card that already has this number."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "phone_taken", "contact_id": str(contact_id)},
    )


async def _notes(session: AsyncSession, contact_id: uuid.UUID) -> list[NoteOut]:
    rows = await session.execute(
        select(ContactNote, User.email)
        .outerjoin(User, User.id == ContactNote.author_id)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc(), ContactNote.id.desc())
    )
    return [_note_out(note, email) for note, email in rows.tuples()]


def _note_out(note: ContactNote, author_email: str | None) -> NoteOut:
    return NoteOut(
        id=note.id,
        body=note.body,
        author_id=note.author_id,
        author_email=author_email,
        created_at=note.created_at,
        edited_at=note.edited_at,
    )


async def _relations(session: AsyncSession, contact_id: uuid.UUID) -> list[RelationOut]:
    rows = await session.execute(
        select(ContactRelation.related_id, Contact.full_name, ContactRelation.label)
        .join(Contact, Contact.id == ContactRelation.related_id)
        .where(ContactRelation.contact_id == contact_id)
        .order_by(Contact.full_name)
    )
    return [RelationOut(related_id=r, full_name=n, label=lb) for r, n, lb in rows.tuples()]


async def _card(session: AsyncSession, user: CurrentUser, contact: Contact) -> ContactCard:
    with_cases = "cases" in await effective_modules(session, user)
    return ContactCard(
        contact=ContactOut.model_validate(contact),
        notes=await _notes(session, contact.id),
        relations=await _relations(session, contact.id),
        consultations=await consultations_of(session, contact.id),
        questionnaire=Questionnaire.model_validate(contact.questionnaire),
        hints=await hints_for(session, contact),
        cases=await cases_of(session, contact.id) if with_cases else None,
    )


async def _own_note(
    session: AsyncSession, user: CurrentUser, contact_id: uuid.UUID, note_id: uuid.UUID
) -> ContactNote:
    """Employees change only their own notes; the specialist changes any."""
    note = await session.get(ContactNote, note_id)
    if note is None or note.contact_id != contact_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if user.role != UserRole.SPECIALIST and note.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return note


# --- Contacts ------------------------------------------------------------------------


@router.post("/search")
async def search_contacts(body: SearchIn, session: TenantSession) -> SearchOut:
    """By name (Cyrillic or Latin), part of the phone number or Telegram; newest first."""
    query = select(Contact)
    term = body.query.strip().removeprefix("@").strip()
    if term:
        pattern = f"%{term}%"
        conditions = [
            Contact.full_name.ilike(pattern),
            Contact.full_name_latin.ilike(pattern),
            Contact.telegram.ilike(pattern),
        ]
        digits = phone_digits(term)
        if len(digits) >= 3:
            conditions.append(Contact.phone.contains(digits))
        query = query.where(or_(*conditions))
    rows = list(
        await session.scalars(
            query.order_by(Contact.created_at.desc(), Contact.id.desc())
            .offset(body.offset)
            .limit(SEARCH_LIMIT + 1)
        )
    )
    return SearchOut(
        items=[ContactListItem.model_validate(c) for c in rows[:SEARCH_LIMIT]],
        has_more=len(rows) > SEARCH_LIMIT,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ContactOut)
async def create_contact(body: ContactIn, user: Member, session: TenantSession) -> Any:
    fields = body.model_dump()
    await _check_refs(session, fields)
    if (existing := await _phone_owner(session, body.phone)) is not None:
        return _phone_taken(existing)
    contact = Contact(tenant_id=user.tenant_id, created_by=user.id, **fields)
    session.add(contact)
    try:
        await session.flush()
    except IntegrityError:  # the same number saved a moment earlier by someone else
        await session.rollback()
        existing = await _phone_owner(session, body.phone)
        if existing is None:
            raise
        return _phone_taken(existing)
    _audit(session, AuditAction.CONTACT_CREATED, user, contact.id)
    await session.commit()
    await session.refresh(contact)
    return ContactOut.model_validate(contact)


@router.get("/{contact_id}")
async def get_contact(contact_id: uuid.UUID, user: Member, session: TenantSession) -> ContactCard:
    return await _card(session, user, await _contact(session, contact_id))


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID, body: ContactUpdate, user: Member, session: TenantSession
) -> Any:
    contact = await _contact(session, contact_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("full_name") is None:
        changes.pop("full_name", None)  # the name is required: null means «leave as is»
    else:
        changes["full_name"] = changes["full_name"].strip() or contact.full_name
    await _check_refs(session, changes)
    if "phone" in changes and (
        existing := await _phone_owner(session, changes["phone"], exclude=contact.id)
    ):
        return _phone_taken(existing)
    changed = sorted(f for f, v in changes.items() if getattr(contact, f) != v)
    for field, value in changes.items():
        setattr(contact, field, value)
    if changed:
        _audit(
            session, AuditAction.CONTACT_UPDATED, user, contact.id, {"fields": ",".join(changed)}
        )
    await session.commit()
    await session.refresh(contact)
    return ContactOut.model_validate(contact)


# --- Notes ---------------------------------------------------------------------------


@router.post("/{contact_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    contact_id: uuid.UUID, body: NoteIn, user: Member, session: TenantSession
) -> NoteOut:
    contact = await _contact(session, contact_id)
    note = ContactNote(
        tenant_id=user.tenant_id, contact_id=contact.id, body=body.body, author_id=user.id
    )
    session.add(note)
    await session.flush()
    _audit(session, AuditAction.CONTACT_NOTE_ADDED, user, contact.id, {"note": str(note.id)})
    await session.commit()
    await session.refresh(note)
    return _note_out(note, user.email)


@router.patch("/{contact_id}/notes/{note_id}")
async def edit_note(
    contact_id: uuid.UUID, note_id: uuid.UUID, body: NoteIn, user: Member, session: TenantSession
) -> NoteOut:
    note = await _own_note(session, user, contact_id, note_id)
    note.body = body.body
    note.edited_at = datetime.now(UTC)
    _audit(session, AuditAction.CONTACT_NOTE_EDITED, user, contact_id, {"note": str(note.id)})
    await session.commit()
    author = await session.scalar(select(User.email).where(User.id == note.author_id))
    return _note_out(note, author)


@router.delete("/{contact_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    contact_id: uuid.UUID, note_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    note = await _own_note(session, user, contact_id, note_id)
    await session.delete(note)
    _audit(session, AuditAction.CONTACT_NOTE_DELETED, user, contact_id, {"note": str(note_id)})
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Links between contacts ----------------------------------------------------------


@router.post("/{contact_id}/relations", status_code=status.HTTP_201_CREATED)
async def link_contacts(
    contact_id: uuid.UUID, body: RelationIn, user: Member, session: TenantSession
) -> list[RelationOut]:
    if body.related_id == contact_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "self_link")
    contact = await _contact(session, contact_id)
    related = await _contact(session, body.related_id)
    session.add_all(
        [
            ContactRelation(
                tenant_id=user.tenant_id,
                contact_id=contact.id,
                related_id=related.id,
                label=(body.label or "").strip() or None,
            ),
            ContactRelation(
                tenant_id=user.tenant_id,
                contact_id=related.id,
                related_id=contact.id,
                label=(body.reverse_label or "").strip() or None,
            ),
        ]
    )
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "already_linked") from error
    _audit(session, AuditAction.CONTACT_LINKED, user, contact.id, {"related": str(related.id)})
    await session.commit()
    return await _relations(session, contact.id)


@router.delete("/{contact_id}/relations/{related_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_contacts(
    contact_id: uuid.UUID, related_id: uuid.UUID, user: Member, session: TenantSession
) -> Response:
    await _contact(session, contact_id)
    result = await session.execute(
        delete(ContactRelation).where(
            or_(
                (ContactRelation.contact_id == contact_id)
                & (ContactRelation.related_id == related_id),
                (ContactRelation.contact_id == related_id)
                & (ContactRelation.related_id == contact_id),
            )
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    _audit(session, AuditAction.CONTACT_UNLINKED, user, contact_id, {"related": str(related_id)})
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

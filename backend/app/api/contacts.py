"""Contacts (backlog 1.2): the card, search, the duplicate guard by phone and warning by
Telegram, the notes timeline and deleting an empty duplicate. Closed by the «clients»
module. Clients are not linked to each other (backlog 1.15.11): a family filed together is
a submission group of their cases (app/api/cases.py).

Personal data stays out of URLs and logs: search takes its query in the request body,
and audit events carry ids and the names of changed fields only.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, exists, func, or_, select, true
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cases import (
    CaseContents,
    CaseOut,
    case_contents,
    cases_of,
    delete_cases,
    refuse_while_importing,
)
from app.api.client_status import (
    TABS,
    Sort,
    Tab,
    filter_contacts,
    latest_consultation,
    sort_contacts,
    tab_of,
)
from app.api.consultations import (
    ConsultationOut,
    HintOut,
    VisaHistoryOut,
    consultations_of,
    hints_for,
    visa_history_of,
)
from app.api.deps import CurrentUser, TenantSession, effective_modules, require_module
from app.api.documents import DocumentOut, documents_of
from app.api.package_files import remove_objects
from app.core import storage
from app.core.audit import record
from app.core.data_values import TELEGRAM_NICK, telegram_nick
from app.core.days import Day
from app.core.db import BIGINT_MAX
from app.core.documents import object_key
from app.core.employee_limits import LimitKind, open_card, watch
from app.core.names import (
    LATIN_PARTS,
    NAME_PARTS,
    NamePart,
    latin_fields,
    name_fields,
    search_words,
    tidy,
)
from app.core.phone import normalize_phone, phone_search_digits
from app.core.questionnaire import Passport, Questionnaire, has_answers, has_passport
from app.models import (
    AuditAction,
    CaseStage,
    Consultation,
    Contact,
    ContactDocument,
    ContactNote,
    ContactTemplate,
    Decision,
    LeadChannel,
    Messenger,
    Partner,
    Trip,
    User,
    UserRole,
    VaultEntry,
    VisaCase,
    VisaOutcome,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/contacts", tags=["contacts"], dependencies=[Depends(require_module("clients"))]
)

Member = Annotated[CurrentUser, Depends(require_module("clients"))]

EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
SEARCH_LIMIT = 50


# --- Schemas -------------------------------------------------------------------------


class ContactFields(BaseModel):
    # ФИО apart (app/core/names.py): the surname or the given name is required.
    last_name: NamePart | None = None
    first_name: NamePart | None = None
    middle_name: NamePart | None = None
    # As in the passport.
    last_name_latin: NamePart | None = None
    first_name_latin: NamePart | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    telegram: Annotated[str, Field(max_length=64)] | None = None
    messenger: Messenger | None = None
    messenger_contact: Annotated[str, Field(max_length=64)] | None = None
    email: Annotated[str, Field(max_length=320, pattern=EMAIL)] | None = None
    birth_date: Day | None = None
    lead_channel_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    consent_signed_on: Day | None = None
    contract_number: Annotated[str, Field(max_length=50)] | None = None
    contract_signed_on: Day | None = None
    # Kept with the questionnaire; on a change, only the fields sent are changed.
    passport: Passport | None = None

    _name_parts = field_validator(*NAME_PARTS, *LATIN_PARTS)(tidy)

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_phone(value)

    @field_validator("telegram", mode="before")
    @classmethod
    def telegram_without_at(cls, value: Any) -> Any:
        # Before the length check: the «@» or the link in front is not kept, so it does
        # not count.
        if not isinstance(value, str):
            return value
        return telegram_nick(value) or None

    @field_validator("messenger_contact", "email", "contract_number")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ContactIn(ContactFields):
    @model_validator(mode="after")
    def named(self) -> "ContactIn":
        if self.last_name is None and self.first_name is None:
            raise ValueError("the surname or the given name is required")
        return self


class ContactUpdate(ContactFields):
    """Only the fields sent are changed; null clears an optional field. The card keeps
    the surname or the given name (name_required otherwise)."""


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    # The parts joined: what lists show.
    full_name: str
    last_name_latin: str | None
    first_name_latin: str | None
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
    offset: int = Field(default=0, ge=0, le=BIGINT_MAX)
    # Client index (backlog 1.5): the tab and the filters.
    tab: Tab = Tab.ALL
    country_id: uuid.UUID | None = None
    stage: CaseStage | None = None
    channel_id: uuid.UUID | None = None
    registrar_id: uuid.UUID | None = None
    # The column the specialist sorted by (app/api/client_status.py → Sort).
    sort: Sort = Sort.NEWEST


class CaseApplicant(BaseModel):
    contact_id: uuid.UUID
    full_name: str


class CaseBrief(BaseModel):
    """The case a row shows: the latest one in work, otherwise the latest one."""

    id: uuid.UUID
    number: int
    destination_id: uuid.UUID
    purpose_id: uuid.UUID | None
    stage: CaseStage
    outcome: VisaOutcome | None
    submission_on: date | None
    # Cases of the submission group, this one included (1 without a group).
    group_size: int
    # Whose case it is when it is not this contact's own: a case opened from this
    # contact's consultation for someone else (pilot fix 2).
    applicant: CaseApplicant | None = None


class ConsultationBrief(BaseModel):
    held_on: date
    decision: Decision | None
    destination_ids: list[uuid.UUID]


class ContactRow(ContactListItem):
    tab: Tab
    # None without the «cases» module or without cases.
    case: CaseBrief | None = None
    cases_in_work: int = 0
    consultation: ConsultationBrief | None = None


class SearchOut(BaseModel):
    items: list[ContactRow]
    has_more: bool
    # Contacts per tab for this search and these filters.
    counts: dict[Tab, int]


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


class ContactCard(BaseModel):
    contact: ContactOut
    notes: list[NoteOut]
    consultations: list[ConsultationOut]
    questionnaire: Questionnaire
    # The visas of the questionnaire and of the client's cases, as the card shows them
    # (app/core/visa_history.py).
    visa_history: list[VisaHistoryOut]
    # Questionnaire checks against the latest consultation and the cases in work
    # (app/core/consultation_checks.py).
    hints: list[HintOut]
    # None when the user has no «cases» module.
    cases: list[CaseOut] | None
    # Files of the consent and the contract, newest first.
    documents: list[DocumentOut]
    # The specialist may delete a card without client history: a duplicate.
    can_delete: bool


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


async def _phone_conflict(
    session: AsyncSession, phone: str | None, exclude: uuid.UUID | None = None
) -> JSONResponse | None:
    """409 when another card already has this number. It points to that card if the user
    sees it; an employee is only told the number is taken when the card is not theirs
    (migration 0031: the check looks past their view)."""
    if phone is None:
        return None
    query = select(Contact.id).where(Contact.phone == phone)
    if exclude is not None:
        query = query.where(Contact.id != exclude)
    if (owner := await session.scalar(query)) is not None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "phone_taken", "contact_id": str(owner)},
        )
    if await session.scalar(select(func.contact_phone_taken(phone, exclude))):
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": "phone_taken"})
    return None


async def _telegram_conflict(
    session: AsyncSession, telegram: str | None, exclude: uuid.UUID | None = None
) -> JSONResponse | None:
    """409 when another card the user sees has this nick — a warning, not a block (owner's
    decision 2026-11-04): the form offers that card or saving anyway with
    allow_shared_telegram, since a family may share one account. A name typed in the
    field is not a nick and is never a duplicate."""
    if telegram is None or not TELEGRAM_NICK.match(telegram):
        return None
    query = select(Contact.id).where(func.lower(Contact.telegram) == telegram.lower())
    if exclude is not None:
        query = query.where(Contact.id != exclude)
    owner = await session.scalar(query.order_by(Contact.created_at).limit(1))
    if owner is None:
        return None
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "telegram_taken", "contact_id": str(owner)},
    )


def _has(column: Any, term: str) -> Any:
    """The column has the text, whatever the case, as it was typed: «_» and «%» are
    characters of a nick or a number, not wildcards."""
    return column.icontains(term, autoescape=True)


def _named(term: str) -> Any:
    """Every word of the search is in the name, Cyrillic or Latin, in any order: «Дарья
    Ковальчук» finds «Ковальчук Дарья Игоревна» (backlog A2)."""
    return and_(
        *(
            or_(_has(Contact.full_name, word), _has(Contact.full_name_latin, word))
            for word in search_words(term)
        )
    )


async def _notes(session: AsyncSession, contact_id: uuid.UUID) -> list[NoteOut]:
    rows = await session.execute(
        select(ContactNote, User.email)
        .outerjoin(User, User.id == ContactNote.author_id)
        .where(ContactNote.contact_id == contact_id)
        .order_by(ContactNote.created_at.desc(), ContactNote.id.desc())
    )
    return [_note_out(note, email) for note, email in rows]


def _note_out(note: ContactNote, author_email: str | None) -> NoteOut:
    return NoteOut(
        id=note.id,
        body=note.body,
        author_id=note.author_id,
        author_email=author_email,
        created_at=note.created_at,
        edited_at=note.edited_at,
    )


async def _card(session: AsyncSession, user: CurrentUser, contact: Contact) -> ContactCard:
    with_cases = "cases" in await effective_modules(session, user)
    consultations = await consultations_of(session, contact.id)
    if not with_cases:
        # Nor the cases a consultation led to: their number, destination and stage are
        # the case's (audit L3).
        for consultation in consultations:
            consultation.cases = []
    return ContactCard(
        contact=ContactOut.model_validate(contact),
        notes=await _notes(session, contact.id),
        consultations=consultations,
        questionnaire=Questionnaire.model_validate(contact.questionnaire),
        visa_history=await visa_history_of(session, contact, with_cases=with_cases),
        hints=await hints_for(session, contact),
        cases=await cases_of(session, contact.id) if with_cases else None,
        documents=await documents_of(session, contact.id),
        can_delete=user.role == UserRole.SPECIALIST,
    )


async def _own_note(
    session: AsyncSession, user: CurrentUser, contact_id: uuid.UUID, note_id: uuid.UUID
) -> ContactNote:
    """Employees change only their own notes; the specialist changes any. A note is on
    the client's card, so changing one opens the card (Т.14)."""
    note = await session.get(ContactNote, note_id)
    if note is None or note.contact_id != contact_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    await open_card(session, user, contact_id)
    if user.role != UserRole.SPECIALIST and note.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return note


# --- Contacts ------------------------------------------------------------------------


@router.post("/search")
async def search_contacts(body: SearchIn, user: Member, session: TenantSession) -> SearchOut:
    """By name (Cyrillic or Latin, its words in any order), part of the phone number or
    Telegram, within a tab of the client index and its filters; newest first or by the
    sorted column. What a module holds is searched only with that module: without «cases»
    nothing in a case finds, filters or sorts a contact, nor decides their tab (audit L3),
    as the card shows no cases."""
    modules = await effective_modules(session, user)
    with_cases = "cases" in modules
    latest = latest_consultation()
    tab = tab_of(latest, with_cases=with_cases)
    query = select(Contact, tab.label("tab")).outerjoin(latest, true())
    term = telegram_nick(body.query)
    if term:
        # The name is looked for word by word; a nick, a number and a login as typed.
        conditions = [_named(term), _has(Contact.telegram, term)]
        if with_cases:
            # The number the visa center knows the client by (ID, application number…).
            conditions.append(
                exists().where(VisaCase.contact_id == Contact.id, _has(VisaCase.vac_number, term))
            )
        if "vault" in modules:
            # The login of an account in the vault: whose account is on this mailbox.
            conditions.append(
                exists().where(VaultEntry.contact_id == Contact.id, _has(VaultEntry.login, term))
            )
        # «8 029 …» typed the local way finds the number stored with 375.
        for digits in phone_search_digits(term):
            if len(digits) >= 3:
                conditions.append(Contact.phone.contains(digits))
        query = query.where(or_(*conditions))
    query = filter_contacts(
        query,
        latest,
        country_id=body.country_id,
        stage=body.stage,
        channel_id=body.channel_id,
        registrar_id=body.registrar_id,
        with_cases=with_cases,
    )

    tabs = query.with_only_columns(tab.label("tab")).subquery()
    counted = dict(
        (await session.execute(select(tabs.c.tab, func.count()).group_by(tabs.c.tab))).all()
    )
    counts = {t: counted.get(t.value, 0) for t in TABS}
    counts[Tab.ALL] = sum(counts.values())

    if body.tab != Tab.ALL:
        query = query.where(tab == body.tab.value)
    query = sort_contacts(query, latest, body.sort, with_cases)
    rows = (await session.execute(query.offset(body.offset).limit(SEARCH_LIMIT + 1))).all()
    page = rows[:SEARCH_LIMIT]
    return SearchOut(
        items=await _rows(session, [(c, Tab(t)) for c, t in page], with_cases),
        has_more=len(rows) > SEARCH_LIMIT,
        counts=counts,
    )


async def _rows(
    session: AsyncSession, page: list[tuple[Contact, Tab]], with_cases: bool
) -> list[ContactRow]:
    """Each row with its latest consultation and, with the «cases» module, its case."""
    ids = [c.id for c, _ in page]
    if not ids:
        return []
    latest = {
        c.contact_id: c
        for c in await session.scalars(
            select(Consultation)
            .where(Consultation.contact_id.in_(ids))
            .order_by(
                Consultation.contact_id,
                Consultation.held_on.desc(),
                Consultation.created_at.desc(),
            )
            .ext(distinct_on(Consultation.contact_id))
        )
    }
    cases: dict[uuid.UUID, list[VisaCase]] = {}
    group_sizes: dict[uuid.UUID, int] = {}
    # Cases opened from a row's consultations for someone else, and whose they are
    # (pilot fix 2). The same rule as client_status.has_case, which the tabs, filters and
    # sorting use in SQL: keep the two in step. RLS hides what an employee does not see.
    applicants: dict[uuid.UUID, str] = {}
    if with_cases:
        for visa_case in await session.scalars(
            select(VisaCase).where(VisaCase.contact_id.in_(ids)).order_by(VisaCase.number.desc())
        ):
            cases.setdefault(visa_case.contact_id, []).append(visa_case)
        for visa_case, owner, name in await session.execute(
            select(VisaCase, Consultation.contact_id, Contact.full_name)
            .join(Consultation, Consultation.id == VisaCase.consultation_id)
            .join(Contact, Contact.id == VisaCase.contact_id)
            .where(Consultation.contact_id.in_(ids), VisaCase.contact_id != Consultation.contact_id)
            .order_by(VisaCase.number.desc())
        ):
            # Not when the row's contact is an applicant of that consultation too.
            if not any(
                c.consultation_id == visa_case.consultation_id
                for c in cases.get(owner, [])
                if c.contact_id == owner
            ):
                cases.setdefault(owner, []).append(visa_case)
                applicants[visa_case.contact_id] = name
        groups = {c.group_id for cs in cases.values() for c in cs if c.group_id is not None}
        if groups:
            group_sizes = dict(
                (
                    await session.execute(
                        select(VisaCase.group_id, func.count())
                        .where(VisaCase.group_id.in_(groups))
                        .group_by(VisaCase.group_id)
                    )
                ).all()
            )

    result = []
    for contact, tab in page:
        row = ContactRow(**ContactListItem.model_validate(contact).model_dump(), tab=tab)
        if (consultation := latest.get(contact.id)) is not None:
            row.consultation = ConsultationBrief(
                held_on=consultation.held_on,
                decision=consultation.decision,
                destination_ids=consultation.destination_ids,
            )
        # As client_status.shown_case: in work first, the contact's own first, newest first.
        own = sorted(
            cases.get(contact.id, []),
            key=lambda c: (c.stage == CaseStage.RESULT, c.contact_id != contact.id, -c.number),
        )
        in_work = [c for c in own if c.stage != CaseStage.RESULT]
        row.cases_in_work = len(in_work)
        if own:
            c = own[0]
            row.case = CaseBrief(
                id=c.id,
                number=c.number,
                destination_id=c.destination_id,
                purpose_id=c.purpose_id,
                stage=c.stage,
                outcome=c.outcome,
                submission_on=c.submission_on,
                group_size=group_sizes.get(c.group_id, 1) if c.group_id else 1,
                applicant=(
                    CaseApplicant(contact_id=c.contact_id, full_name=applicants[c.contact_id])
                    if c.contact_id != contact.id
                    else None
                ),
            )
        result.append(row)
    return result


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ContactOut)
async def create_contact(
    body: ContactIn, user: Member, session: TenantSession, allow_shared_telegram: bool = False
) -> Any:
    fields = body.model_dump(exclude={"passport", *NAME_PARTS, *LATIN_PARTS})
    await _check_refs(session, fields)
    if (conflict := await _phone_conflict(session, body.phone)) is not None:
        return conflict
    if not allow_shared_telegram and (conflict := await _telegram_conflict(session, body.telegram)):
        return conflict
    contact = Contact(
        tenant_id=user.tenant_id,
        created_by=user.id,
        **fields,
        **name_fields(body.last_name, body.first_name, body.middle_name),
        **latin_fields(body.last_name_latin, body.first_name_latin),
    )
    if body.passport is not None:
        contact.questionnaire = Questionnaire.model_validate(body.passport.model_dump()).model_dump(
            mode="json"
        )
    session.add(contact)
    try:
        await session.flush()
    except IntegrityError:
        # Changed by someone else a moment after the checks: the same number saved, or the
        # channel or the partner removed.
        await session.rollback()
        if (conflict := await _phone_conflict(session, body.phone)) is not None:
            return conflict
        await _check_refs(session, fields)
        raise
    _audit(session, AuditAction.CONTACT_CREATED, user, contact.id)
    await session.commit()
    await session.refresh(contact)
    return ContactOut.model_validate(contact)


@router.get("/{contact_id}")
async def get_contact(contact_id: uuid.UUID, user: Member, session: TenantSession) -> ContactCard:
    contact = await _contact(session, contact_id)
    # An employee's open counts against their daily limit and goes to the log (Т.14).
    await watch(session, user, LimitKind.CARDS, "contact", contact.id, AuditAction.CONTACT_VIEWED)
    return await _card(session, user, contact)


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdate,
    user: Member,
    session: TenantSession,
    allow_shared_telegram: bool = False,
) -> Any:
    contact = await _contact(session, contact_id)
    # The answer is the card, so an edit opens it too (Т.14).
    await watch(session, user, LimitKind.CARDS, "contact", contact.id, AuditAction.CONTACT_VIEWED)
    changes = body.model_dump(exclude_unset=True, exclude={"passport"})
    if changes.keys() & set(NAME_PARTS):
        name = {f: changes.get(f, getattr(contact, f)) for f in NAME_PARTS}
        if name["last_name"] is None and name["first_name"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name_required")
        changes |= name_fields(**name)
    if changes.keys() & set(LATIN_PARTS):
        changes |= latin_fields(**{f: changes.get(f, getattr(contact, f)) for f in LATIN_PARTS})
    await _check_refs(session, changes)
    if "phone" in changes and (
        conflict := await _phone_conflict(session, changes["phone"], exclude=contact.id)
    ):
        return conflict
    # Only a new nick is checked: a card already sharing one is not stopped on every edit.
    new_telegram = changes.get("telegram")
    if (
        not allow_shared_telegram
        and new_telegram is not None
        and new_telegram.lower() != (contact.telegram or "").lower()
        and (conflict := await _telegram_conflict(session, new_telegram, exclude=contact.id))
    ):
        return conflict
    changed = [f for f, v in changes.items() if getattr(contact, f) != v]
    for field, value in changes.items():
        setattr(contact, field, value)
    if body.passport is not None:
        current = Questionnaire.model_validate(contact.questionnaire)
        passport = body.passport.model_dump(exclude_unset=True)
        changed += [f for f, v in passport.items() if getattr(current, f) != v]
        contact.questionnaire = current.model_copy(update=passport).model_dump(mode="json")
    changed.sort()
    if changed:
        _audit(
            session, AuditAction.CONTACT_UPDATED, user, contact.id, {"fields": ",".join(changed)}
        )
    await session.commit()
    await session.refresh(contact)
    return ContactOut.model_validate(contact)


class ContactDeletion(CaseContents):
    """What the question before a card is deleted names (pilot remark 1.15.13): everything
    that goes with it, since a card with client history goes at once too (owner's decision
    2026-11-24). The counts of its cases are summed over all of them."""

    # Numbers of the client's cases, in order.
    cases: list[int]
    consultations: int
    paid_consultations: int
    notes: int
    vault_entries: int
    # Consent and contract files: they leave the storage too.
    documents: int
    trips: int
    templates: int
    # Kept on the card itself: the passport's data page and the questionnaire answers.
    passport: bool
    questionnaire: bool


async def _deletable(session: AsyncSession, user: CurrentUser, contact_id: uuid.UUID) -> Contact:
    """Another workspace's card is not found; an employee may not delete one."""
    contact = await _contact(session, contact_id)
    if user.role != UserRole.SPECIALIST:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return contact


@router.get("/{contact_id}/deletion")
async def contact_deletion(
    contact_id: uuid.UUID, user: Member, session: TenantSession
) -> ContactDeletion:
    """What goes with the card, for the question before it is deleted."""
    contact = await _deletable(session, user, contact_id)

    async def count(model: Any, *where: Any) -> int:
        query = select(func.count()).select_from(model)
        return await session.scalar(query.where(model.contact_id == contact.id, *where)) or 0

    numbers = await session.scalars(
        select(VisaCase.number).where(VisaCase.contact_id == contact.id).order_by(VisaCase.number)
    )
    questionnaire = Questionnaire.model_validate(contact.questionnaire)
    return ContactDeletion(
        **(await case_contents(session, VisaCase.contact_id == contact.id)).model_dump(),
        cases=list(numbers),
        consultations=await count(Consultation),
        paid_consultations=await count(Consultation, Consultation.fee_paid),
        notes=await count(ContactNote),
        vault_entries=await count(VaultEntry),
        documents=await count(ContactDocument),
        trips=await count(Trip),
        templates=await count(ContactTemplate),
        passport=has_passport(questionnaire),
        questionnaire=has_answers(questionnaire),
    )


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(contact_id: uuid.UUID, user: Member, session: TenantSession) -> Response:
    """A card goes at once with all the client's history (owner's decisions 2026-11-04 and
    2026-11-24 → «Удаление кейса и карточки со всем содержимым»): the cases as delete_case
    takes them, consultations, notes, vault accounts, consent and contract files, trips and
    templates given to the client, by the foreign keys. The specialist only; the question
    before it names all of it (contact_deletion). The files leave the storage after the
    commit."""
    assert user.tenant_id is not None
    contact = await _deletable(session, user, contact_id)
    refuse_while_importing(user.tenant_id)
    # Locked to the commit: a row being saved for the card right now (a case, a file, an
    # account, a trip, a template) is waited for and goes with the card, a file with its
    # object; one saved later finds the card gone.
    await session.execute(select(Contact.id).where(Contact.id == contact.id).with_for_update())
    cases = list(
        await session.scalars(
            select(VisaCase).where(VisaCase.contact_id == contact.id).order_by(VisaCase.number)
        )
    )
    files = list(
        await session.scalars(
            select(ContactDocument.id).where(ContactDocument.contact_id == contact.id)
        )
    )
    portal_keys = await delete_cases(session, user, cases)
    await session.execute(delete(Contact).where(Contact.id == contact.id))
    _audit(
        session,
        AuditAction.CONTACT_DELETED,
        user,
        contact.id,
        {"cases": len(cases), "files": len(files)},
    )
    await session.commit()
    for document_id in files:
        try:
            await storage.delete_object(object_key(user.tenant_id, document_id))
        except (storage.StorageNotConfigured, storage.StorageFailed):
            # The rows are gone; what is left in the storage is ciphertext nobody refers
            # to. Ids only in the log.
            logger.warning("Object of document %s of a deleted card was not removed", document_id)
    await remove_objects(portal_keys, "deleted card")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Notes ---------------------------------------------------------------------------


@router.post("/{contact_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    contact_id: uuid.UUID, body: NoteIn, user: Member, session: TenantSession
) -> NoteOut:
    contact = await _contact(session, contact_id)
    await open_card(session, user, contact.id)
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

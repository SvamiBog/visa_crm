"""The vault of accounts (backlog 1.7): logins and encrypted passwords of clients'
accounts on visa center and embassy sites, mailboxes, Booking; and the workspace's own
accounts — the specialist's backup mailboxes and the accounts made on each.

Closed by the «clients» and «vault» modules: an employee sees passwords only when the
specialist gives them the vault. A password is decrypted only by an explicit «show»,
and every showing goes to the audit log. Logins, passwords and notes never go into the
log or a URL: events carry ids and the system's name.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module
from app.core.audit import record
from app.core.passwords import PasswordRules, generate_password
from app.core.vault import decrypt_for_tenant, encrypt_for_tenant, get_tenant_key
from app.models import (
    AuditAction,
    Contact,
    EmailOwner,
    VaultEntry,
    VaultSystem,
    VisaCase,
)

router = APIRouter(
    tags=["vault"],
    dependencies=[Depends(require_module("clients")), Depends(require_module("vault"))],
)

Member = Annotated[CurrentUser, Depends(require_module("vault"))]

Login = Annotated[str, Field(min_length=1, max_length=320)]
# Spaces may be part of a password, so it is kept exactly as typed.
Password = Annotated[str, Field(min_length=1, max_length=200)]
ExtraId = Annotated[str, Field(max_length=100)]
Note = Annotated[str, Field(max_length=1000)]
DEFAULT_RULES = PasswordRules(length=14, lower=True, upper=True, digits=True, symbols="!@#$%*?")


# --- Schemas -------------------------------------------------------------------------


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_is_none(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


class EntryIn(BaseModel):
    system_id: uuid.UUID
    # The applicant whose account it is; none — the workspace's own account.
    contact_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    login: Login
    email_owner: EmailOwner | None = None
    password: Password | None = None
    created_manually: bool = False
    extra_id: ExtraId | None = None
    note: Note | None = None

    _login = field_validator("login", mode="before")(_strip)
    _optional = field_validator("extra_id", "note", mode="before")(_blank_is_none)


class EntryUpdate(BaseModel):
    """Only the fields sent are changed; null clears an optional field (a null password
    removes the stored one). The owner of the account does not change."""

    system_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    login: Login | None = None
    email_owner: EmailOwner | None = None
    password: Password | None = None
    created_manually: bool | None = None
    extra_id: ExtraId | None = None
    note: Note | None = None

    _login = field_validator("login", mode="before")(_strip)
    _optional = field_validator("extra_id", "note", mode="before")(_blank_is_none)


class EntryOut(BaseModel):
    """Without the password: it is shown only by an explicit «show» (reveal)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    system_id: uuid.UUID
    contact_id: uuid.UUID | None
    case_id: uuid.UUID | None
    login: str
    email_owner: EmailOwner | None
    has_password: bool
    password_updated_at: datetime | None
    created_manually: bool
    extra_id: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class AccountOnMailbox(BaseModel):
    """A client's account made on one of the workspace's own mailboxes."""

    id: uuid.UUID
    system_id: uuid.UUID
    contact_id: uuid.UUID
    contact_name: str


class OwnEntryOut(EntryOut):
    accounts: list[AccountOnMailbox]


class PasswordOut(BaseModel):
    password: str


class GenerateIn(BaseModel):
    # The site whose rules to follow; none — a strong password by default rules.
    system_id: uuid.UUID | None = None


# --- Helpers -------------------------------------------------------------------------


def entry_out(entry: VaultEntry) -> EntryOut:
    return EntryOut.model_validate(
        {
            **{f: getattr(entry, f) for f in EntryOut.model_fields if f != "has_password"},
            "has_password": entry.password_encrypted is not None,
        }
    )


def _audit(
    session: AsyncSession, action: AuditAction, user: CurrentUser, entry: VaultEntry, **extra: Any
) -> None:
    """Ids and the system's name only: no login, password or note."""
    details: dict[str, Any] = {**extra}
    if entry.contact_id is not None:
        details["contact"] = str(entry.contact_id)
    record(
        session, action, actor=user, target_type="vault_entry", target_id=entry.id, details=details
    )


async def _get[T](session: AsyncSession, model: type[T], item_id: uuid.UUID) -> T:
    """RLS hides other workspaces' rows, so someone else's id is simply not found."""
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


async def _check_case(
    session: AsyncSession, case_id: uuid.UUID | None, contact_id: uuid.UUID | None
) -> None:
    """An account is linked only to a case of its own applicant."""
    if case_id is None:
        return
    case = await _get(session, VisaCase, case_id)
    if case.contact_id != contact_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "case_of_another_contact")


async def _set_password(
    session: AsyncSession, user: CurrentUser, entry: VaultEntry, password: str | None
) -> None:
    assert user.tenant_id is not None
    if password is None:
        entry.password_encrypted = entry.password_updated_at = None
        return
    key = await get_tenant_key(session, user.tenant_id)
    entry.password_encrypted = encrypt_for_tenant(key, user.tenant_id, entry.id, password)
    entry.password_updated_at = datetime.now(UTC)


def _no_store(response: Response) -> None:
    """A password must not stay in a browser or proxy cache."""
    response.headers["Cache-Control"] = "no-store"


# --- Accounts ------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/vault")
async def contact_vault(contact_id: uuid.UUID, session: TenantSession) -> list[EntryOut]:
    await _get(session, Contact, contact_id)
    rows = await session.scalars(
        select(VaultEntry)
        .join(VaultSystem, VaultSystem.id == VaultEntry.system_id)
        .where(VaultEntry.contact_id == contact_id)
        .order_by(VaultSystem.sort_order, VaultSystem.name, VaultEntry.created_at)
    )
    return [entry_out(e) for e in rows]


@router.get("/vault/own")
async def own_vault(session: TenantSession) -> list[OwnEntryOut]:
    """The workspace's own accounts; for each, the clients' accounts made on it (the
    same login, e.g. a backup mailbox)."""
    own = list(
        await session.scalars(
            select(VaultEntry)
            .join(VaultSystem, VaultSystem.id == VaultEntry.system_id)
            .where(VaultEntry.contact_id.is_(None))
            .order_by(VaultSystem.sort_order, VaultSystem.name, VaultEntry.login)
        )
    )
    logins = {e.login.lower() for e in own}
    accounts: dict[str, list[AccountOnMailbox]] = {}
    if logins:
        login = func.lower(VaultEntry.login)
        rows = await session.execute(
            select(login, VaultEntry.id, VaultEntry.system_id, Contact.id, Contact.full_name)
            .join(Contact, Contact.id == VaultEntry.contact_id)
            .where(login.in_(logins))
            .order_by(Contact.full_name)
        )
        for key, entry_id, system_id, contact_id, name in rows.tuples():
            accounts.setdefault(key, []).append(
                AccountOnMailbox(
                    id=entry_id, system_id=system_id, contact_id=contact_id, contact_name=name
                )
            )
    return [
        OwnEntryOut(**entry_out(e).model_dump(), accounts=accounts.get(e.login.lower(), []))
        for e in own
    ]


@router.post("/vault/entries", status_code=status.HTTP_201_CREATED)
async def create_entry(body: EntryIn, user: Member, session: TenantSession) -> EntryOut:
    system = await _get(session, VaultSystem, body.system_id)
    if body.contact_id is not None:
        await _get(session, Contact, body.contact_id)
    await _check_case(session, body.case_id, body.contact_id)
    # The id is set first: the password is encrypted bound to it.
    entry = VaultEntry(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        created_by=user.id,
        **body.model_dump(exclude={"password"}),
    )
    if body.password is not None:
        await _set_password(session, user, entry, body.password)
    session.add(entry)
    await session.flush()
    _audit(session, AuditAction.VAULT_ENTRY_CREATED, user, entry, name=system.name)
    await session.commit()
    await session.refresh(entry)
    return entry_out(entry)


@router.patch("/vault/entries/{entry_id}")
async def update_entry(
    entry_id: uuid.UUID, body: EntryUpdate, user: Member, session: TenantSession
) -> EntryOut:
    entry = await _get(session, VaultEntry, entry_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("system_id", "login", "created_manually"):
        if field in changes and changes[field] is None:
            del changes[field]  # required: null means «leave as is»
    if "system_id" in changes:
        await _get(session, VaultSystem, changes["system_id"])
    if "case_id" in changes:
        await _check_case(session, changes["case_id"], entry.contact_id)
    changed = sorted(f for f, v in changes.items() if f == "password" or getattr(entry, f) != v)
    if "password" in changes:
        await _set_password(session, user, entry, changes.pop("password"))
    for field, value in changes.items():
        setattr(entry, field, value)
    if changed:
        system = await _get(session, VaultSystem, entry.system_id)
        _audit(
            session,
            AuditAction.VAULT_ENTRY_UPDATED,
            user,
            entry,
            name=system.name,
            fields=",".join(changed),
        )
    await session.commit()
    await session.refresh(entry)
    return entry_out(entry)


@router.delete("/vault/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(entry_id: uuid.UUID, user: Member, session: TenantSession) -> None:
    entry = await _get(session, VaultEntry, entry_id)
    system = await _get(session, VaultSystem, entry.system_id)
    _audit(session, AuditAction.VAULT_ENTRY_DELETED, user, entry, name=system.name)
    await session.delete(entry)
    await session.commit()


@router.post("/vault/entries/{entry_id}/reveal")
async def reveal_password(
    entry_id: uuid.UUID, user: Member, session: TenantSession, response: Response
) -> PasswordOut:
    """The password in clear text. Every call is written to the audit log."""
    assert user.tenant_id is not None
    entry = await _get(session, VaultEntry, entry_id)
    if entry.password_encrypted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_password")
    key = await get_tenant_key(session, user.tenant_id)
    password = decrypt_for_tenant(key, user.tenant_id, entry.id, entry.password_encrypted)
    system = await _get(session, VaultSystem, entry.system_id)
    _audit(session, AuditAction.VAULT_PASSWORD_REVEALED, user, entry, name=system.name)
    await session.commit()
    _no_store(response)
    return PasswordOut(password=password)


@router.post("/vault/password")
async def new_password(body: GenerateIn, session: TenantSession, response: Response) -> PasswordOut:
    """A new password by the site's rules, to set on the site and then save here."""
    rules = DEFAULT_RULES
    if body.system_id is not None:
        system = await _get(session, VaultSystem, body.system_id)
        rules = PasswordRules(
            length=system.password_length,
            lower=system.password_lower,
            upper=system.password_upper,
            digits=system.password_digits,
            symbols=system.password_symbols,
        )
    _no_store(response)
    return PasswordOut(password=generate_password(rules))

"""A specialist's own reference lists (backlog 1.1): visa purposes, lead channels, partners
with terms, registrars with prices, prepayments, currencies and tax; the systems of the
vault with their password rules (backlog 1.7).

Everyone in the workspace reads them (forms need them); only the specialist edits.
Everything runs in the workspace scope, so Row-Level Security keeps other workspaces out.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_specialist, require_tenant_user
from app.core.audit import record
from app.core.money import CENT, CURRENCIES, Amount, Currency, Percent
from app.models import (
    AuditAction,
    Country,
    Hall,
    LeadChannel,
    Partner,
    PartnerTerms,
    PartnerTermsType,
    Prepayment,
    PurposeKind,
    Registrar,
    RegistrarKind,
    RegistrarPrice,
    Tenant,
    VaultSystem,
    VaultSystemKind,
    VisaPurpose,
)

router = APIRouter(
    prefix="/dictionaries", tags=["dictionaries"], dependencies=[Depends(require_tenant_user)]
)

Specialist = Annotated[CurrentUser, Depends(require_specialist)]
Reader = Annotated[CurrentUser, Depends(require_tenant_user)]

Name = Annotated[str, Field(min_length=1, max_length=100)]
LongName = Annotated[str, Field(min_length=1, max_length=200)]
Contact = Annotated[str, Field(max_length=200)]
Note = Annotated[str, Field(max_length=1000)]


# --- Schemas -------------------------------------------------------------------------


class EntryIn(BaseModel):
    name: Name
    sort_order: int = Field(default=0, ge=0, le=999)
    is_active: bool = True


class EntryUpdate(BaseModel):
    name: Name | None = None
    sort_order: int | None = Field(default=None, ge=0, le=999)
    is_active: bool | None = None


class EntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool


class PurposeIn(EntryIn):
    kind: PurposeKind | None = None


class PurposeUpdate(EntryUpdate):
    kind: PurposeKind | None = None


class PurposeOut(EntryOut):
    kind: PurposeKind | None


class TermsIn(BaseModel):
    country_id: uuid.UUID | None = None  # None: every country without its own terms
    terms_type: PartnerTermsType
    percent: Percent | None = None
    amount: Amount | None = None
    currency: Currency | None = None

    @model_validator(mode="after")
    def fields_match_type(self) -> "TermsIn":
        if (self.terms_type == PartnerTermsType.PERCENT) != (self.percent is not None):
            raise ValueError("percent goes with the percent terms only")
        fixed = self.terms_type == PartnerTermsType.FIXED
        if fixed != (self.amount is not None) or fixed != (self.currency is not None):
            raise ValueError("amount and currency go with the fixed terms only")
        return self


class TermsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_id: uuid.UUID | None
    terms_type: PartnerTermsType
    percent: Decimal | None
    amount: Decimal | None
    currency: str | None


class PartnerIn(BaseModel):
    name: LongName
    contact: Contact | None = None
    note: Note | None = None
    is_active: bool = True


class PartnerUpdate(BaseModel):
    name: LongName | None = None
    contact: Contact | None = None
    note: Note | None = None
    is_active: bool | None = None


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact: str | None
    note: str | None
    is_active: bool
    created_at: datetime
    terms: list[TermsOut]


class PriceIn(BaseModel):
    country_id: uuid.UUID
    purpose_id: uuid.UUID | None = None  # None: any visa purpose
    hall: Hall = Hall.STANDARD
    amount: Amount
    currency: Currency


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_id: uuid.UUID
    purpose_id: uuid.UUID | None
    hall: Hall
    amount: Decimal
    currency: str


# Remind when the advance with a registrar falls below this; 0 — only when in debt.
Threshold = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2),
    AfterValidator(lambda value: value.quantize(CENT)),
]


class RegistrarIn(BaseModel):
    name: LongName
    kind: RegistrarKind
    contact: Contact | None = None
    note: Note | None = None
    is_active: bool = True
    # The currency of the settlements; omitted — the workspace's base currency.
    currency: Currency | None = None
    low_balance: Threshold | None = None


class RegistrarUpdate(BaseModel):
    name: LongName | None = None
    kind: RegistrarKind | None = None
    contact: Contact | None = None
    note: Note | None = None
    is_active: bool | None = None
    currency: Currency | None = None
    low_balance: Threshold | None = None


class RegistrarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: RegistrarKind
    contact: str | None
    note: str | None
    is_active: bool
    currency: str
    low_balance: Decimal | None
    created_at: datetime
    prices: list[PriceOut]


class PrepaymentIn(BaseModel):
    country_id: uuid.UUID
    purpose_id: uuid.UUID | None = None
    amount: Amount


class PrepaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    country_id: uuid.UUID
    purpose_id: uuid.UUID | None
    amount: Decimal


# Special characters a site may accept: printable ASCII other than letters and digits.
Symbols = Annotated[str, Field(max_length=32, pattern=r"^[!-/:-@\[-`{-~]*$")]
Url = Annotated[str, Field(max_length=300, pattern=r"^https?://\S+$")]


def _blank_is_none(value: str | None) -> str | None:
    return (value or "").strip() or None


def _unique_chars(value: str | None) -> str | None:
    return None if value is None else "".join(dict.fromkeys(value))


class SystemIn(BaseModel):
    name: Name
    kind: VaultSystemKind
    url: Url | None = None
    password_length: int = Field(default=12, ge=6, le=64)
    password_lower: bool = True
    password_upper: bool = True
    password_digits: bool = True
    password_symbols: Symbols = "!@#$%*?"
    sort_order: int = Field(default=0, ge=0, le=999)
    is_active: bool = True

    _url = field_validator("url", mode="before")(_blank_is_none)
    _symbols = field_validator("password_symbols")(_unique_chars)


class SystemUpdate(BaseModel):
    """Only the fields sent are changed; null clears the link."""

    name: Name | None = None
    kind: VaultSystemKind | None = None
    url: Url | None = None
    password_length: int | None = Field(default=None, ge=6, le=64)
    password_lower: bool | None = None
    password_upper: bool | None = None
    password_digits: bool | None = None
    password_symbols: Symbols | None = None
    sort_order: int | None = Field(default=None, ge=0, le=999)
    is_active: bool | None = None

    _url = field_validator("url", mode="before")(_blank_is_none)
    _symbols = field_validator("password_symbols")(_unique_chars)


class SystemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: VaultSystemKind
    url: str | None
    password_length: int
    password_lower: bool
    password_upper: bool
    password_digits: bool
    password_symbols: str
    sort_order: int
    is_active: bool


class MoneyOut(BaseModel):
    """Currencies and tax. The base currency and the tax rate are set by the admin."""

    base_currency: str
    payment_currencies: list[str]
    # Exchange rates: National Bank of the Republic of Belarus on the payment date.
    rate_source: Literal["nbrb"] = "nbrb"
    tax_rate: Decimal


class MoneyUpdate(BaseModel):
    payment_currencies: list[Currency] = Field(min_length=1)


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    target_type: str,
    target_id: uuid.UUID,
    name: str | None = None,
) -> None:
    """In the workspace's own log; only ids and the entry's name, no client data."""
    record(
        session,
        action,
        actor=user,
        target_type=target_type,
        target_id=target_id,
        details={"name": name} if name else None,
    )


async def _get[T](session: AsyncSession, model: type[T], item_id: uuid.UUID) -> T:
    """RLS hides other workspaces' rows, so someone else's id is simply not found."""
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


async def _flush_or_conflict(session: AsyncSession, code: str) -> None:
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, code) from error


def _apply(item: Any, changes: dict[str, Any], nullable: frozenset[str] = frozenset()) -> None:
    """Set the fields sent; a null for a required field means «leave as is»."""
    for field, value in changes.items():
        if value is not None or field in nullable:
            setattr(item, field, value)


async def _check_countries(session: AsyncSession, ids: set[uuid.UUID]) -> None:
    found = set(await session.scalars(select(Country.id).where(Country.id.in_(ids))))
    if ids - found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def _check_purposes(session: AsyncSession, ids: set[uuid.UUID]) -> None:
    found = set(await session.scalars(select(VisaPurpose.id).where(VisaPurpose.id.in_(ids))))
    if ids - found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def _unique_keys(keys: list[tuple[Any, ...]]) -> None:
    """A list sent as a whole must not repeat the same key (country, purpose, …)."""
    if len(set(keys)) != len(keys):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_entry")


# --- Visa purposes and lead channels -------------------------------------------------

SimpleModel = type[VisaPurpose] | type[LeadChannel]


def _simple_list_routes(
    path: str,
    model: SimpleModel,
    kind: str,
    schemas: tuple[type[EntryIn], type[EntryUpdate], type[EntryOut]],
):
    entry_in, entry_update, entry_out = schemas

    @router.get(f"/{path}", name=f"list_{path}")
    async def list_entries(session: TenantSession) -> list[entry_out]:
        rows = await session.scalars(select(model).order_by(model.sort_order, model.name))
        return [entry_out.model_validate(r) for r in rows]

    @router.post(f"/{path}", status_code=status.HTTP_201_CREATED, name=f"create_{path}")
    async def create_entry(body: entry_in, user: Specialist, session: TenantSession) -> entry_out:
        entry = model(tenant_id=user.tenant_id, **body.model_dump())
        entry.name = entry.name.strip()
        session.add(entry)
        await _flush_or_conflict(session, "name_taken")
        _audit(session, AuditAction.REFERENCE_CREATED, user, kind, entry.id, entry.name)
        await session.commit()
        return entry_out.model_validate(entry)

    @router.patch(f"/{path}/{{entry_id}}", name=f"update_{path}")
    async def update_entry(
        entry_id: uuid.UUID, body: entry_update, user: Specialist, session: TenantSession
    ) -> entry_out:
        entry = await _get(session, model, entry_id)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("name") is not None:
            changes["name"] = changes["name"].strip()
        # A purpose's kind can be taken back: then it gets the items for any purpose.
        _apply(entry, changes, nullable=frozenset({"kind"}))
        await _flush_or_conflict(session, "name_taken")
        _audit(session, AuditAction.REFERENCE_UPDATED, user, kind, entry.id, entry.name)
        await session.commit()
        return entry_out.model_validate(entry)


_simple_list_routes("purposes", VisaPurpose, "visa_purpose", (PurposeIn, PurposeUpdate, PurposeOut))
_simple_list_routes("channels", LeadChannel, "lead_channel", (EntryIn, EntryUpdate, EntryOut))


# --- Vault systems -------------------------------------------------------------------


def _check_password_rules(system: VaultSystem) -> None:
    if not (
        system.password_lower
        or system.password_upper
        or system.password_digits
        or system.password_symbols
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "password_rules_empty")


@router.get("/vault-systems")
async def list_systems(session: TenantSession) -> list[SystemOut]:
    rows = await session.scalars(
        select(VaultSystem).order_by(VaultSystem.sort_order, VaultSystem.name)
    )
    return [SystemOut.model_validate(r) for r in rows]


@router.post("/vault-systems", status_code=status.HTTP_201_CREATED)
async def create_system(body: SystemIn, user: Specialist, session: TenantSession) -> SystemOut:
    system = VaultSystem(tenant_id=user.tenant_id, **body.model_dump())
    system.name = system.name.strip()
    _check_password_rules(system)
    session.add(system)
    await _flush_or_conflict(session, "name_taken")
    _audit(session, AuditAction.REFERENCE_CREATED, user, "vault_system", system.id, system.name)
    await session.commit()
    return SystemOut.model_validate(system)


@router.patch("/vault-systems/{system_id}")
async def update_system(
    system_id: uuid.UUID, body: SystemUpdate, user: Specialist, session: TenantSession
) -> SystemOut:
    system = await _get(session, VaultSystem, system_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        changes["name"] = changes["name"].strip()
    _apply(system, changes, nullable=frozenset({"url"}))
    _check_password_rules(system)
    await _flush_or_conflict(session, "name_taken")
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "vault_system", system.id, system.name)
    await session.commit()
    return SystemOut.model_validate(system)


# --- Partners ------------------------------------------------------------------------


@router.get("/partners")
async def list_partners(session: TenantSession) -> list[PartnerOut]:
    rows = await session.scalars(select(Partner).order_by(Partner.name))
    return [PartnerOut.model_validate(p) for p in rows]


@router.post("/partners", status_code=status.HTTP_201_CREATED)
async def create_partner(body: PartnerIn, user: Specialist, session: TenantSession) -> PartnerOut:
    partner = Partner(tenant_id=user.tenant_id, **body.model_dump())
    session.add(partner)
    await session.flush()
    _audit(session, AuditAction.REFERENCE_CREATED, user, "partner", partner.id, partner.name)
    await session.commit()
    await session.refresh(partner, ["terms"])
    return PartnerOut.model_validate(partner)


@router.patch("/partners/{partner_id}")
async def update_partner(
    partner_id: uuid.UUID, body: PartnerUpdate, user: Specialist, session: TenantSession
) -> PartnerOut:
    partner = await _get(session, Partner, partner_id)
    _apply(partner, body.model_dump(exclude_unset=True), nullable=frozenset({"contact", "note"}))
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "partner", partner.id, partner.name)
    await session.commit()
    return PartnerOut.model_validate(partner)


@router.put("/partners/{partner_id}/terms")
async def replace_partner_terms(
    partner_id: uuid.UUID, body: list[TermsIn], user: Specialist, session: TenantSession
) -> PartnerOut:
    """Replace all terms: one per country plus, optionally, a default (country_id null)."""
    partner = await _get(session, Partner, partner_id)
    _unique_keys([(t.country_id,) for t in body])
    await _check_countries(session, {t.country_id for t in body if t.country_id is not None})
    # Delete first: the new list may reuse a country the old one had (unique key).
    await session.execute(delete(PartnerTerms).where(PartnerTerms.partner_id == partner.id))
    session.expire(partner, ["terms"])
    session.add_all(
        PartnerTerms(tenant_id=user.tenant_id, partner_id=partner.id, **t.model_dump())
        for t in body
    )
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "partner", partner.id, partner.name)
    await session.commit()
    await session.refresh(partner, ["terms"])
    return PartnerOut.model_validate(partner)


# --- Registrars ----------------------------------------------------------------------


@router.get("/registrars")
async def list_registrars(session: TenantSession) -> list[RegistrarOut]:
    rows = await session.scalars(select(Registrar).order_by(Registrar.name))
    return [RegistrarOut.model_validate(r) for r in rows]


@router.post("/registrars", status_code=status.HTTP_201_CREATED)
async def create_registrar(
    body: RegistrarIn, user: Specialist, session: TenantSession
) -> RegistrarOut:
    values = body.model_dump()
    values["currency"] = values["currency"] or (await _tenant(session, user)).base_currency
    registrar = Registrar(tenant_id=user.tenant_id, **values)
    session.add(registrar)
    await session.flush()
    _audit(session, AuditAction.REFERENCE_CREATED, user, "registrar", registrar.id, registrar.name)
    await session.commit()
    await session.refresh(registrar, ["prices"])
    return RegistrarOut.model_validate(registrar)


@router.patch("/registrars/{registrar_id}")
async def update_registrar(
    registrar_id: uuid.UUID, body: RegistrarUpdate, user: Specialist, session: TenantSession
) -> RegistrarOut:
    registrar = await _get(session, Registrar, registrar_id)
    _apply(
        registrar,
        body.model_dump(exclude_unset=True),
        nullable=frozenset({"contact", "note", "low_balance"}),
    )
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "registrar", registrar.id, registrar.name)
    await session.commit()
    return RegistrarOut.model_validate(registrar)


@router.put("/registrars/{registrar_id}/prices")
async def replace_registrar_prices(
    registrar_id: uuid.UUID, body: list[PriceIn], user: Specialist, session: TenantSession
) -> RegistrarOut:
    """Replace the price list: one fee per country, visa purpose and hall."""
    registrar = await _get(session, Registrar, registrar_id)
    _unique_keys([(p.country_id, p.purpose_id, p.hall) for p in body])
    await _check_countries(session, {p.country_id for p in body})
    await _check_purposes(session, {p.purpose_id for p in body if p.purpose_id is not None})
    # Delete first: the new list may reuse a key the old one had (unique key).
    await session.execute(delete(RegistrarPrice).where(RegistrarPrice.registrar_id == registrar.id))
    session.expire(registrar, ["prices"])
    session.add_all(
        RegistrarPrice(tenant_id=user.tenant_id, registrar_id=registrar.id, **p.model_dump())
        for p in body
    )
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "registrar", registrar.id, registrar.name)
    await session.commit()
    await session.refresh(registrar, ["prices"])
    return RegistrarOut.model_validate(registrar)


# --- Prepayments ---------------------------------------------------------------------


@router.get("/prepayments")
async def list_prepayments(session: TenantSession) -> list[PrepaymentOut]:
    rows = await session.scalars(select(Prepayment))
    return [PrepaymentOut.model_validate(p) for p in rows]


@router.put("/prepayments")
async def replace_prepayments(
    body: list[PrepaymentIn], user: Specialist, session: TenantSession
) -> list[PrepaymentOut]:
    """Replace the list: one amount (in the base currency) per country and visa purpose."""
    _unique_keys([(p.country_id, p.purpose_id) for p in body])
    await _check_countries(session, {p.country_id for p in body})
    await _check_purposes(session, {p.purpose_id for p in body if p.purpose_id is not None})
    await session.execute(delete(Prepayment))  # RLS: only this workspace's rows
    rows = [Prepayment(tenant_id=user.tenant_id, **p.model_dump()) for p in body]
    session.add_all(rows)
    assert user.tenant_id is not None
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "prepayments", user.tenant_id)
    await session.commit()
    return [PrepaymentOut.model_validate(p) for p in rows]


# --- Currencies and tax --------------------------------------------------------------


def _ordered_currencies(base: str, chosen: list[str]) -> list[str]:
    """The base currency first (payments in it are always possible), then the rest."""
    return [base] + [c for c in CURRENCIES if c in chosen and c != base]


def _money(tenant: Tenant) -> MoneyOut:
    return MoneyOut(
        base_currency=tenant.base_currency,
        payment_currencies=_ordered_currencies(tenant.base_currency, tenant.payment_currencies),
        tax_rate=tenant.tax_rate,
    )


async def _tenant(session: AsyncSession, user: CurrentUser) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None  # the session dependency already checked the workspace
    return tenant


@router.get("/money")
async def get_money(user: Reader, session: TenantSession) -> MoneyOut:
    return _money(await _tenant(session, user))


@router.patch("/money")
async def update_money(body: MoneyUpdate, user: Specialist, session: TenantSession) -> MoneyOut:
    tenant = await _tenant(session, user)
    tenant.payment_currencies = _ordered_currencies(tenant.base_currency, body.payment_currencies)
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "currencies", tenant.id)
    await session.commit()
    return _money(tenant)

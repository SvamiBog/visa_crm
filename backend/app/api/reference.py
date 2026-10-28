"""Countries and visa center operators (shared, the admin edits them) and the workspace's
directions: appointment rules «destination × submission country» with their cities.

Every workspace user reads them (forms need them, so no module check here). Directions are
set up by the specialist herself (backlog 1.20): an entry a case uses is switched off, an
unused one can be deleted. Shared entries are switched off, never deleted.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Select, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import (
    CurrentUser,
    SystemSession,
    TenantSession,
    require_admin,
    require_specialist,
)
from app.core.audit import record
from app.core.directions import city_key, new_cities
from app.models import (
    AuditAction,
    BookingType,
    Country,
    CountryRule,
    VacIdentifierType,
    VacOperator,
    VisaCase,
    VisaCenter,
)

router = APIRouter(prefix="/reference", tags=["reference"])
admin_router = APIRouter(
    prefix="/admin/reference", tags=["admin"], dependencies=[Depends(require_admin)]
)

Admin = Annotated[CurrentUser, Depends(require_admin)]
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

Name = Annotated[str, Field(min_length=1, max_length=100)]
Days = Annotated[int, Field(ge=1, le=365)]


# --- Schemas -------------------------------------------------------------------------


PassportAge = Annotated[int, Field(ge=1, le=30)]
PassportMargin = Annotated[int, Field(ge=0, le=24)]


class CountryIn(BaseModel):
    code: Annotated[str, Field(pattern=r"^[A-Za-z]{2}$")]
    name_ru: Name
    name_en: Name
    consultation_minutes: Annotated[int, Field(ge=5, le=600)] | None = None
    passport_max_age_years: PassportAge | None = None
    passport_valid_after_months: PassportMargin | None = None
    schengen_since: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def upper_code(self) -> "CountryIn":
        self.code = self.code.upper()
        return self


class CountryUpdate(BaseModel):
    """Only the fields sent are changed."""

    code: Annotated[str, Field(pattern=r"^[A-Za-z]{2}$")] | None = None
    name_ru: Name | None = None
    name_en: Name | None = None
    consultation_minutes: Annotated[int, Field(ge=5, le=600)] | None = None
    passport_max_age_years: PassportAge | None = None
    passport_valid_after_months: PassportMargin | None = None
    schengen_since: date | None = None
    is_active: bool | None = None


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name_ru: str
    name_en: str
    consultation_minutes: int | None
    passport_max_age_years: int | None
    passport_valid_after_months: int | None
    schengen_since: date | None
    is_active: bool


class OperatorIn(BaseModel):
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{2,32}$")]
    name: Name
    is_active: bool = True


class OperatorUpdate(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_active: bool


# A direction is set up for up to every destination at once; cities come in a short list.
MAX_DESTINATIONS = 60
MAX_CITIES = 50
Cities = Annotated[list[Name], Field(max_length=MAX_CITIES)]


class CitiesIn(BaseModel):
    cities: Annotated[list[Name], Field(min_length=1, max_length=MAX_CITIES)]


class VisaCenterUpdate(BaseModel):
    """Only the fields sent are changed; the English name may be cleared."""

    city_ru: Name | None = None
    city_en: Name | None = None
    is_active: bool | None = None


class VisaCenterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    city_ru: str
    city_en: str | None
    is_active: bool


class RuleFields(BaseModel):
    window_days: Days | None = None
    vac_operator_id: uuid.UUID | None = None
    represented_by_id: uuid.UUID | None = None
    identifier_type: VacIdentifierType | None = None
    appeals_accepted: bool | None = None
    appeal_deadline_days: Days | None = None
    notes: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool = True


class RuleIn(RuleFields):
    """One direction set up for several destinations at once, with its cities."""

    destination_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=MAX_DESTINATIONS)]
    submission_id: uuid.UUID
    booking_type: BookingType
    cities: Cities = []

    @model_validator(mode="after")
    def different_countries(self) -> "RuleIn":
        if self.submission_id in self.destination_ids:
            raise ValueError("a country cannot be its own submission country")
        self.destination_ids = list(dict.fromkeys(self.destination_ids))
        return self


class RuleUpdate(BaseModel):
    """Only the fields sent are changed. The pair of countries is fixed."""

    booking_type: BookingType | None = None
    window_days: Days | None = None
    vac_operator_id: uuid.UUID | None = None
    represented_by_id: uuid.UUID | None = None
    identifier_type: VacIdentifierType | None = None
    appeals_accepted: bool | None = None
    appeal_deadline_days: Days | None = None
    notes: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    destination_id: uuid.UUID
    submission_id: uuid.UUID
    booking_type: BookingType
    window_days: int | None
    vac_operator_id: uuid.UUID | None
    represented_by_id: uuid.UUID | None
    identifier_type: VacIdentifierType | None
    appeals_accepted: bool | None
    appeal_deadline_days: int | None
    notes: str | None
    is_active: bool
    updated_at: datetime
    visa_centers: list[VisaCenterOut]


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    user: CurrentUser,
    target_type: str,
    target_id: uuid.UUID,
    name: str,
) -> None:
    """Shared entries go to the admin's log (no workspace), a direction to its workspace's
    log; only ids and the entry's name."""
    record(
        session,
        action,
        actor=user,
        tenant_id=user.tenant_id,
        target_type=target_type,
        target_id=target_id,
        details={"name": name},
    )


async def _flush_or_conflict(session: AsyncSession, code: str) -> None:
    """Write pending changes; a unique key taken by another entry is a 409 with `code`."""
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, code) from error


async def _get[T](session: AsyncSession, model: type[T], item_id: uuid.UUID) -> T:
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


def _normalize_rule(rule: CountryRule) -> None:
    """Fields that only make sense with another field are cleared or required."""
    if rule.booking_type != BookingType.WINDOWED_APPLICATION:
        rule.window_days = None
    elif rule.window_days is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "window_days_required")
    if rule.appeals_accepted is not True:
        rule.appeal_deadline_days = None


async def _rule_name(session: AsyncSession, rule: CountryRule) -> str:
    codes = dict(
        (
            await session.execute(
                select(Country.id, Country.code).where(
                    Country.id.in_([rule.destination_id, rule.submission_id])
                )
            )
        ).all()
    )
    return f"{codes.get(rule.destination_id)} ← {codes.get(rule.submission_id)}"


async def _in_use(session: AsyncSession, rule: CountryRule) -> bool:
    """A case of the pair still counts on the rule (its window, its appeals)."""
    query = exists().where(
        VisaCase.destination_id == rule.destination_id,
        VisaCase.submission_country_id == rule.submission_id,
    )
    return bool(await session.scalar(select(query)))


async def _add_cities(
    session: AsyncSession, user: CurrentUser, rule: CountryRule, cities: list[str]
) -> None:
    have = await session.scalars(select(VisaCenter.city_ru).where(VisaCenter.rule_id == rule.id))
    for city in new_cities(have, cities):
        center = VisaCenter(tenant_id=rule.tenant_id, rule_id=rule.id, city_ru=city)
        session.add(center)
        # A double click adds the same city twice at once: the second gets a 409.
        await _flush_or_conflict(session, "visa_center_exists")
        _audit(session, AuditAction.REFERENCE_CREATED, user, "visa_center", center.id, city)


async def _rule_out(session: AsyncSession, rule: CountryRule) -> RuleOut:
    # updated_at is set by the database on update; the cities may have changed too.
    await session.refresh(rule)
    await session.refresh(rule, ["visa_centers"])
    return RuleOut.model_validate(rule)


def _rules_query() -> Select[tuple[CountryRule]]:
    """Rules ordered by destination name, as the lists show them."""
    return (
        select(CountryRule)
        .join(Country, Country.id == CountryRule.destination_id)
        .order_by(Country.name_ru)
    )


def _apply(item: Any, changes: dict[str, Any], nullable: frozenset[str] = frozenset()) -> None:
    """Set the fields sent; a null for a required field means «leave as is»."""
    for field, value in changes.items():
        if value is not None or field in nullable:
            setattr(item, field, value)


REF_MODELS: dict[str, type[Country] | type[VacOperator]] = {
    "destination_id": Country,
    "submission_id": Country,
    "represented_by_id": Country,
    "vac_operator_id": VacOperator,
}


async def _check_refs(session: AsyncSession, fields: dict[str, Any]) -> None:
    """Every id must point to an existing country or operator (404 otherwise).
    Checked before the rule changes, so no half-applied change is flushed."""
    for field, model in REF_MODELS.items():
        if fields.get(field) is not None:
            await _get(session, model, fields[field])


RULE_NULLABLE = frozenset(
    {
        "window_days",
        "vac_operator_id",
        "represented_by_id",
        "identifier_type",
        "appeals_accepted",
        "appeal_deadline_days",
        "notes",
    }
)


# --- Workspace: read -----------------------------------------------------------------


@router.get("/countries")
async def list_countries(session: TenantSession) -> list[CountryOut]:
    rows = await session.scalars(select(Country).where(Country.is_active).order_by(Country.name_ru))
    return [CountryOut.model_validate(c) for c in rows]


@router.get("/operators")
async def list_operators(session: TenantSession) -> list[OperatorOut]:
    rows = await session.scalars(
        select(VacOperator).where(VacOperator.is_active).order_by(VacOperator.name)
    )
    return [OperatorOut.model_validate(o) for o in rows]


@router.get("/rules")
async def list_rules(
    session: TenantSession,
    submission: Annotated[uuid.UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[RuleOut]:
    """The workspace's directions. Forms get what is on; the builder asks for the
    switched-off entries too."""
    # A switched-off country takes its rules out of the workspace lists too.
    submission_country = aliased(Country)
    query = (
        _rules_query()
        .join(submission_country, submission_country.id == CountryRule.submission_id)
        .where(Country.is_active, submission_country.is_active)
    )
    if not include_inactive:
        query = query.where(CountryRule.is_active)
    if submission is not None:
        query = query.where(CountryRule.submission_id == submission)
    rules = []
    for rule in await session.scalars(query):
        out = RuleOut.model_validate(rule)
        if not include_inactive:
            out.visa_centers = [c for c in out.visa_centers if c.is_active]
        rules.append(out)
    return rules


# --- Workspace: the specialist sets up her directions ---------------------------------


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rules(body: RuleIn, user: Specialist, session: TenantSession) -> list[RuleOut]:
    """The same direction for every destination sent; all or nothing."""
    fields = body.model_dump(exclude={"destination_ids", "cities"})
    await _check_refs(session, fields)
    for destination_id in body.destination_ids:
        await _get(session, Country, destination_id)
    rules = []
    for destination_id in body.destination_ids:
        rule = CountryRule(tenant_id=user.tenant_id, destination_id=destination_id, **fields)
        _normalize_rule(rule)
        session.add(rule)
        rules.append(rule)
    await _flush_or_conflict(session, "rule_exists")
    for rule in rules:
        name = await _rule_name(session, rule)
        _audit(session, AuditAction.REFERENCE_CREATED, user, "country_rule", rule.id, name)
        await _add_cities(session, user, rule, body.cities)
    await session.commit()
    return [await _rule_out(session, rule) for rule in rules]


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID, body: RuleUpdate, user: Specialist, session: TenantSession
) -> RuleOut:
    rule = await _get(session, CountryRule, rule_id)
    changes = body.model_dump(exclude_unset=True)
    await _check_refs(session, changes)
    _apply(rule, changes, nullable=RULE_NULLABLE)
    _normalize_rule(rule)
    name = await _rule_name(session, rule)
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "country_rule", rule.id, name)
    await session.commit()
    return await _rule_out(session, rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, user: Specialist, session: TenantSession) -> Response:
    """Only a direction no case uses; otherwise it is switched off."""
    rule = await _get(session, CountryRule, rule_id)
    if await _in_use(session, rule):
        raise HTTPException(status.HTTP_409_CONFLICT, "rule_in_use")
    name = await _rule_name(session, rule)
    await session.delete(rule)
    _audit(session, AuditAction.REFERENCE_DELETED, user, "country_rule", rule.id, name)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rules/{rule_id}/visa-centers")
async def add_visa_centers(
    rule_id: uuid.UUID, body: CitiesIn, user: Specialist, session: TenantSession
) -> RuleOut:
    """Cities the direction does not have yet: «Минск» and «минск» are one city."""
    rule = await _get(session, CountryRule, rule_id)
    await _add_cities(session, user, rule, body.cities)
    await session.commit()
    return await _rule_out(session, rule)


@router.patch("/visa-centers/{center_id}")
async def update_visa_center(
    center_id: uuid.UUID, body: VisaCenterUpdate, user: Specialist, session: TenantSession
) -> VisaCenterOut:
    center = await _get(session, VisaCenter, center_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("city_ru") is not None:
        changes["city_ru"] = " ".join(changes["city_ru"].split())
        others = await session.scalars(
            select(VisaCenter.city_ru).where(
                VisaCenter.rule_id == center.rule_id, VisaCenter.id != center.id
            )
        )
        if city_key(changes["city_ru"]) in {city_key(c) for c in others}:
            raise HTTPException(status.HTTP_409_CONFLICT, "visa_center_exists")
    _apply(center, changes, nullable=frozenset({"city_en"}))
    await _flush_or_conflict(session, "visa_center_exists")
    _audit(session, AuditAction.REFERENCE_UPDATED, user, "visa_center", center.id, center.city_ru)
    await session.commit()
    return VisaCenterOut.model_validate(center)


@router.delete("/visa-centers/{center_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_visa_center(
    center_id: uuid.UUID, user: Specialist, session: TenantSession
) -> Response:
    """Only a city no case names; otherwise it is switched off."""
    center = await _get(session, VisaCenter, center_id)
    if await session.scalar(select(exists().where(VisaCase.visa_center_id == center.id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "visa_center_in_use")
    await session.delete(center)
    _audit(session, AuditAction.REFERENCE_DELETED, user, "visa_center", center.id, center.city_ru)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Admin: read and edit ------------------------------------------------------------


@admin_router.get("/countries")
async def admin_list_countries(session: SystemSession) -> list[CountryOut]:
    rows = await session.scalars(select(Country).order_by(Country.name_ru))
    return [CountryOut.model_validate(c) for c in rows]


@admin_router.post("/countries", status_code=status.HTTP_201_CREATED)
async def create_country(body: CountryIn, admin: Admin, session: SystemSession) -> CountryOut:
    country = Country(**body.model_dump())
    session.add(country)
    await _flush_or_conflict(session, "country_code_taken")
    _audit(session, AuditAction.REFERENCE_CREATED, admin, "country", country.id, country.code)
    await session.commit()
    return CountryOut.model_validate(country)


@admin_router.patch("/countries/{country_id}")
async def update_country(
    country_id: uuid.UUID, body: CountryUpdate, admin: Admin, session: SystemSession
) -> CountryOut:
    country = await _get(session, Country, country_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("code") is not None:
        changes["code"] = changes["code"].upper()
    _apply(
        country,
        changes,
        nullable=frozenset(
            {
                "consultation_minutes",
                "passport_max_age_years",
                "passport_valid_after_months",
                "schengen_since",
            }
        ),
    )
    await _flush_or_conflict(session, "country_code_taken")
    _audit(session, AuditAction.REFERENCE_UPDATED, admin, "country", country.id, country.code)
    await session.commit()
    return CountryOut.model_validate(country)


@admin_router.get("/operators")
async def admin_list_operators(session: SystemSession) -> list[OperatorOut]:
    rows = await session.scalars(select(VacOperator).order_by(VacOperator.name))
    return [OperatorOut.model_validate(o) for o in rows]


@admin_router.post("/operators", status_code=status.HTTP_201_CREATED)
async def create_operator(body: OperatorIn, admin: Admin, session: SystemSession) -> OperatorOut:
    operator = VacOperator(**body.model_dump())
    session.add(operator)
    await _flush_or_conflict(session, "operator_code_taken")
    _audit(
        session, AuditAction.REFERENCE_CREATED, admin, "vac_operator", operator.id, operator.name
    )
    await session.commit()
    return OperatorOut.model_validate(operator)


@admin_router.patch("/operators/{operator_id}")
async def update_operator(
    operator_id: uuid.UUID, body: OperatorUpdate, admin: Admin, session: SystemSession
) -> OperatorOut:
    operator = await _get(session, VacOperator, operator_id)
    _apply(operator, body.model_dump(exclude_unset=True))
    _audit(
        session, AuditAction.REFERENCE_UPDATED, admin, "vac_operator", operator.id, operator.name
    )
    await session.commit()
    return OperatorOut.model_validate(operator)

"""Shared reference: countries, visa center operators and appointment rules.

Workspaces read it (forms need the country list, so no module check here); the admin
edits it in the admin panel. Entries are switched off, never deleted: cases refer to them.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, SystemSession, TenantSession, require_admin
from app.core.audit import record
from app.models import (
    AuditAction,
    BookingType,
    Country,
    CountryRule,
    VacIdentifierType,
    VacOperator,
    VisaCenter,
)

router = APIRouter(prefix="/reference", tags=["reference"])
admin_router = APIRouter(
    prefix="/admin/reference", tags=["admin"], dependencies=[Depends(require_admin)]
)

Admin = Annotated[CurrentUser, Depends(require_admin)]

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


class VisaCenterIn(BaseModel):
    city_ru: Name
    city_en: Name
    is_active: bool = True


class VisaCenterUpdate(BaseModel):
    city_ru: Name | None = None
    city_en: Name | None = None
    is_active: bool | None = None


class VisaCenterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    city_ru: str
    city_en: str
    is_active: bool


class RuleFields(BaseModel):
    window_days: Days | None = None
    vac_operator_id: uuid.UUID | None = None
    represented_by_id: uuid.UUID | None = None
    identifier_type: VacIdentifierType | None = None
    appeals_accepted: bool | None = None
    appeal_deadline_days: Days | None = None
    is_verified: bool = False
    notes: Annotated[str, Field(max_length=1000)] | None = None
    is_active: bool = True


class RuleIn(RuleFields):
    destination_id: uuid.UUID
    submission_id: uuid.UUID
    booking_type: BookingType

    @model_validator(mode="after")
    def different_countries(self) -> "RuleIn":
        if self.destination_id == self.submission_id:
            raise ValueError("a country cannot be its own submission country")
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
    is_verified: bool | None = None
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
    is_verified: bool
    notes: str | None
    is_active: bool
    updated_at: datetime
    visa_centers: list[VisaCenterOut]


# --- Helpers -------------------------------------------------------------------------


def _audit(
    session: AsyncSession,
    action: AuditAction,
    admin: CurrentUser,
    target_type: str,
    target_id: uuid.UUID,
    name: str,
) -> None:
    """Platform-level event (no workspace): visible in the admin's log."""
    record(
        session,
        action,
        actor=admin,
        tenant_id=None,
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
    session: TenantSession, submission: Annotated[uuid.UUID | None, Query()] = None
) -> list[RuleOut]:
    # A switched-off country takes its rules out of the workspace lists too.
    submission_country = aliased(Country)
    query = (
        _rules_query()
        .join(submission_country, submission_country.id == CountryRule.submission_id)
        .where(CountryRule.is_active, Country.is_active, submission_country.is_active)
    )
    if submission is not None:
        query = query.where(CountryRule.submission_id == submission)
    rules = []
    for rule in await session.scalars(query):
        out = RuleOut.model_validate(rule)
        out.visa_centers = [c for c in out.visa_centers if c.is_active]
        rules.append(out)
    return rules


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


@admin_router.get("/rules")
async def admin_list_rules(
    session: SystemSession, submission: Annotated[uuid.UUID | None, Query()] = None
) -> list[RuleOut]:
    query = _rules_query()
    if submission is not None:
        query = query.where(CountryRule.submission_id == submission)
    return [RuleOut.model_validate(r) for r in await session.scalars(query)]


@admin_router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, admin: Admin, session: SystemSession) -> RuleOut:
    await _check_refs(session, body.model_dump())
    rule = CountryRule(**body.model_dump())
    _normalize_rule(rule)
    session.add(rule)
    await _flush_or_conflict(session, "rule_exists")
    name = await _rule_name(session, rule)
    _audit(session, AuditAction.REFERENCE_CREATED, admin, "country_rule", rule.id, name)
    await session.commit()
    return await _rule_out(session, rule)


@admin_router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID, body: RuleUpdate, admin: Admin, session: SystemSession
) -> RuleOut:
    rule = await _get(session, CountryRule, rule_id)
    changes = body.model_dump(exclude_unset=True)
    await _check_refs(session, changes)
    _apply(rule, changes, nullable=RULE_NULLABLE)
    _normalize_rule(rule)
    name = await _rule_name(session, rule)
    _audit(session, AuditAction.REFERENCE_UPDATED, admin, "country_rule", rule.id, name)
    await session.commit()
    return await _rule_out(session, rule)


@admin_router.post("/rules/{rule_id}/visa-centers", status_code=status.HTTP_201_CREATED)
async def create_visa_center(
    rule_id: uuid.UUID, body: VisaCenterIn, admin: Admin, session: SystemSession
) -> VisaCenterOut:
    await _get(session, CountryRule, rule_id)
    center = VisaCenter(rule_id=rule_id, **body.model_dump())
    session.add(center)
    await _flush_or_conflict(session, "visa_center_exists")
    _audit(session, AuditAction.REFERENCE_CREATED, admin, "visa_center", center.id, center.city_ru)
    await session.commit()
    return VisaCenterOut.model_validate(center)


@admin_router.patch("/visa-centers/{center_id}")
async def update_visa_center(
    center_id: uuid.UUID, body: VisaCenterUpdate, admin: Admin, session: SystemSession
) -> VisaCenterOut:
    center = await _get(session, VisaCenter, center_id)
    _apply(center, body.model_dump(exclude_unset=True))
    await _flush_or_conflict(session, "visa_center_exists")
    _audit(session, AuditAction.REFERENCE_UPDATED, admin, "visa_center", center.id, center.city_ru)
    await session.commit()
    return VisaCenterOut.model_validate(center)

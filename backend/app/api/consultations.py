"""Consultations and the questionnaire (backlog 1.3). Closed by the «clients» module.

Creating cases from a consultation («Создать кейс») comes with cases (backlog 1.4).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSession, require_module, workspace_today
from app.core.audit import record
from app.core.consultation_checks import Destination, check
from app.core.money import Amount, Currency
from app.core.questionnaire import Questionnaire, Refusal
from app.models import (
    AuditAction,
    Consultation,
    Contact,
    Country,
    Decision,
    LeadChannel,
    Tenant,
    VisaCase,
    VisaOutcome,
)

router = APIRouter(tags=["consultations"], dependencies=[Depends(require_module("clients"))])

Member = Annotated[CurrentUser, Depends(require_module("clients"))]


class ConsultationFields(BaseModel):
    applicants: int = Field(default=1, ge=1, le=30)
    decision: Decision | None = None
    lead_channel_id: uuid.UUID | None = None
    destination_ids: Annotated[list[uuid.UUID], Field(max_length=10)] = []
    trip_planned_on: date | None = None
    follow_up_on: date | None = None
    fee_amount: Amount | None = None
    fee_currency: Currency | None = None
    fee_paid: bool = False
    fee_paid_on: date | None = None
    notes: Annotated[str, Field(max_length=5000)] | None = None


class ConsultationIn(ConsultationFields):
    held_on: date

    @model_validator(mode="after")
    def fee_is_consistent(self) -> "ConsultationIn":
        _check_fee(self.fee_amount, self.fee_currency, self.fee_paid, self.fee_paid_on)
        return self


class ConsultationUpdate(BaseModel):
    """Only the fields sent are changed; null clears an optional field."""

    held_on: date | None = None
    applicants: int | None = Field(default=None, ge=1, le=30)
    decision: Decision | None = None
    lead_channel_id: uuid.UUID | None = None
    destination_ids: Annotated[list[uuid.UUID], Field(max_length=10)] | None = None
    trip_planned_on: date | None = None
    follow_up_on: date | None = None
    fee_amount: Amount | None = None
    fee_currency: Currency | None = None
    fee_paid: bool | None = None
    fee_paid_on: date | None = None
    notes: Annotated[str, Field(max_length=5000)] | None = None


class ConsultationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    held_on: date
    applicants: int
    decision: Decision | None
    lead_channel_id: uuid.UUID | None
    destination_ids: list[uuid.UUID]
    trip_planned_on: date | None
    follow_up_on: date | None
    fee_amount: Decimal | None
    fee_currency: str | None
    fee_paid: bool
    fee_paid_on: date | None
    notes: str | None
    created_at: datetime

    @computed_field
    @property
    def month(self) -> str:
        """Month of the request, for monthly reports: never typed by hand."""
        return self.held_on.strftime("%Y-%m")


class HintOut(BaseModel):
    code: str
    country: str | None


def _check_fee(
    amount: Decimal | None, currency: str | None, paid: bool, paid_on: date | None
) -> None:
    if (amount is None) != (currency is None):
        raise ValueError("the fee needs both an amount and a currency")
    if paid_on is not None and not paid:
        raise ValueError("a payment date means the fee is paid")


def _audit(
    session: AsyncSession, action: AuditAction, user: CurrentUser, consultation: Consultation
) -> None:
    record(
        session,
        action,
        actor=user,
        target_type="consultation",
        target_id=consultation.id,
        details={"contact": str(consultation.contact_id)},
    )


async def _contact(session: AsyncSession, contact_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return contact


async def _check_refs(session: AsyncSession, fields: dict[str, Any]) -> None:
    """Channels must be this workspace's (RLS), countries must exist."""
    channel = fields.get("lead_channel_id")
    if channel is not None and await session.get(LeadChannel, channel) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    ids = set(fields.get("destination_ids") or [])
    if ids:
        found = set(await session.scalars(select(Country.id).where(Country.id.in_(ids))))
        if ids - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def _check_currency(session: AsyncSession, user: CurrentUser, currency: str | None) -> None:
    """A fee only in a currency the specialist accepts (Lists → Currencies and tax)."""
    if currency is None:
        return
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    if currency != tenant.base_currency and currency not in tenant.payment_currencies:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "currency_not_accepted")


# --- Read helpers for the contact card (app/api/contacts.py) --------------------------


async def consultations_of(session: AsyncSession, contact_id: uuid.UUID) -> list[ConsultationOut]:
    rows = await session.scalars(
        select(Consultation)
        .where(Consultation.contact_id == contact_id)
        .order_by(Consultation.held_on.desc(), Consultation.created_at.desc())
    )
    return [ConsultationOut.model_validate(c) for c in rows]


async def hints_for(session: AsyncSession, contact: Contact) -> list[HintOut]:
    """Questionnaire checks against the countries of the latest consultation and of the
    cases still in work, and the trip date of that consultation, with «today» in the
    specialist's time zone. Refusals from the contact's cases count as in the
    questionnaire: a new case for the same country is warned about."""
    latest = await session.scalar(
        select(Consultation)
        .where(Consultation.contact_id == contact.id)
        .order_by(Consultation.held_on.desc(), Consultation.created_at.desc())
        .limit(1)
    )
    cases = list(
        await session.scalars(
            select(VisaCase).where(VisaCase.contact_id == contact.id).order_by(VisaCase.number)
        )
    )
    destination_ids = list(latest.destination_ids) if latest is not None else []
    destination_ids += [c.destination_id for c in cases if c.outcome is None]
    destination_ids = list(dict.fromkeys(destination_ids))  # unique, in order
    refused = [c for c in cases if c.outcome == VisaOutcome.REFUSAL]
    wanted = set(destination_ids) | {c.destination_id for c in refused}
    countries = (
        {c.id: c for c in await session.scalars(select(Country).where(Country.id.in_(wanted)))}
        if wanted
        else {}
    )
    destinations = [
        Destination(
            code=countries[i].code,
            passport_max_age_years=countries[i].passport_max_age_years,
            passport_valid_after_months=countries[i].passport_valid_after_months,
        )
        for i in destination_ids
        if i in countries
    ]
    questionnaire = Questionnaire.model_validate(contact.questionnaire)
    case_refusals = [
        Refusal(
            country=countries[c.destination_id].code,
            year=c.decided_on.year if c.decided_on else None,
        )
        for c in refused
        if c.destination_id in countries
    ]
    if case_refusals:
        questionnaire = questionnaire.model_copy(
            update={"refusals": questionnaire.refusals + case_refusals}
        )
    hints = check(
        questionnaire,
        destinations,
        trip_on=latest.trip_planned_on if latest else None,
        today=await workspace_today(session, contact.tenant_id),
    )
    return [HintOut(code=h.code, country=h.country) for h in hints]


# --- Endpoints -----------------------------------------------------------------------


@router.post("/contacts/{contact_id}/consultations", status_code=status.HTTP_201_CREATED)
async def create_consultation(
    contact_id: uuid.UUID, body: ConsultationIn, user: Member, session: TenantSession
) -> ConsultationOut:
    contact = await _contact(session, contact_id)
    fields = body.model_dump()
    if fields["lead_channel_id"] is None:
        fields["lead_channel_id"] = contact.lead_channel_id
    await _check_refs(session, fields)
    await _check_currency(session, user, body.fee_currency)
    consultation = Consultation(
        tenant_id=user.tenant_id, contact_id=contact.id, created_by=user.id, **fields
    )
    session.add(consultation)
    await session.flush()
    _audit(session, AuditAction.CONSULTATION_CREATED, user, consultation)
    await session.commit()
    await session.refresh(consultation)
    return ConsultationOut.model_validate(consultation)


@router.patch("/consultations/{consultation_id}")
async def update_consultation(
    consultation_id: uuid.UUID, body: ConsultationUpdate, user: Member, session: TenantSession
) -> ConsultationOut:
    consultation = await session.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    changes = body.model_dump(exclude_unset=True)
    for required in ("held_on", "applicants", "fee_paid", "destination_ids"):
        if changes.get(required, ...) is None:
            changes.pop(required)  # null means «leave as is» for required fields
    await _check_refs(session, changes)
    await _check_currency(session, user, changes.get("fee_currency"))
    for field, value in changes.items():
        setattr(consultation, field, value)
    try:
        _check_fee(
            consultation.fee_amount,
            consultation.fee_currency,
            consultation.fee_paid,
            consultation.fee_paid_on,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation") from error
    _audit(session, AuditAction.CONSULTATION_UPDATED, user, consultation)
    await session.commit()
    await session.refresh(consultation)
    return ConsultationOut.model_validate(consultation)


@router.put("/contacts/{contact_id}/questionnaire")
async def replace_questionnaire(
    contact_id: uuid.UUID, body: Questionnaire, user: Member, session: TenantSession
) -> list[HintOut]:
    """Save the questionnaire answers; returns the checks against the latest consultation."""
    contact = await _contact(session, contact_id)
    contact.questionnaire = body.model_dump(mode="json")
    record(
        session,
        AuditAction.QUESTIONNAIRE_UPDATED,
        actor=user,
        target_type="contact",
        target_id=contact.id,
    )
    await session.commit()
    return await hints_for(session, contact)

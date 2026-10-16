"""Consultation (консультация): one conversation with a contact about a visa.

Spec: docs/clients-module.md → «Консультация». The questionnaire answers live on the
contact (app/core/questionnaire.py); a consultation keeps what was decided this time.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Decision(StrEnum):
    """Решение по визе. NULL: not decided at the consultation."""

    YES = "yes"
    NO = "no"
    THINKING = "thinking"


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_consultations_{column}_same_tenant",
    )


class Consultation(Base):
    __tablename__ = "consultations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_consultations_id_tenant"),
        _same_tenant("contact_id", "contacts", "CASCADE"),
        _same_tenant("lead_channel_id", "lead_channels", "RESTRICT"),
        Index("ix_consultations_contact_held", "contact_id", "held_on"),
        CheckConstraint("applicants BETWEEN 1 AND 30", name="applicants_range"),
        CheckConstraint(
            "(fee_amount IS NULL) = (fee_currency IS NULL)", name="fee_amount_with_currency"
        ),
        CheckConstraint("fee_amount IS NULL OR fee_amount > 0", name="fee_positive"),
        CheckConstraint("fee_paid_on IS NULL OR fee_paid", name="paid_on_only_when_paid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    held_on: Mapped[date] = mapped_column(Date)
    applicants: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    decision: Mapped[Decision | None] = mapped_column(
        Enum(Decision, name="consultation_decision", values_callable=lambda e: [m.value for m in e])
    )
    # Taken from the contact, can be changed: conversion by channel.
    lead_channel_id: Mapped[uuid.UUID | None]
    # Where and when the client wants to go (country ids from the shared reference).
    destination_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), server_default=text("'{}'")
    )
    trip_planned_on: Mapped[date | None] = mapped_column(Date)
    # Consultation fee: counted towards the service price whenever the client comes back.
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fee_currency: Mapped[str | None] = mapped_column(String(3))
    fee_paid: Mapped[bool] = mapped_column(server_default=text("false"))
    fee_paid_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

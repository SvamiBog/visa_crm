"""Countries and visa center operators — shared reference data — and each workspace's
directions: appointment rules «destination × submission country» with their cities.

Countries and operators are common to the whole platform, so no tenant_id: every workspace
reads them, only the admin changes them (Row-Level Security of migration 0009). Directions
are the workspace's own: every specialist works her own countries and cities (backlog 1.20,
docs/decisions.md → «Свои направления визовика»).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class BookingType(StrEnum):
    """How a client gets an appointment for a destination in a submission country."""

    WAITING_LIST = "waiting_list"  # лист ожидания: a registrar puts the client in a queue
    WINDOWED_APPLICATION = "windowed_application"  # заявка с окном (France: 60 days)
    DIRECT_SLOT = "direct_slot"  # прямой слот on the visa center's site
    EMBASSY = "embassy"  # appointment directly with the embassy


class VacIdentifierType(StrEnum):
    """What the visa center calls the client's number in its queue."""

    VAC_ID = "vac_id"  # ID: Poland, Latvia, Italy
    APPLICATION_NUMBER = "application_number"  # Germany, Austria, Belgium, …
    FORM_NUMBER = "form_number"  # embassy form number: France
    USERNAME = "username"  # Greece


class VacOperator(Base):
    """Visa center operator: VFS Global, TLScontact, BLS…"""

    __tablename__ = "vac_operators"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stable key for code that works per operator (slot connectors, stage 6).
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Country(Base):
    __tablename__ = "countries"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{2}$'", name="code_iso_alpha2"),
        CheckConstraint(
            "consultation_minutes IS NULL OR consultation_minutes BETWEEN 5 AND 600",
            name="consultation_minutes_range",
        ),
        CheckConstraint(
            "passport_max_age_years IS NULL OR passport_max_age_years BETWEEN 1 AND 30",
            name="passport_max_age_range",
        ),
        CheckConstraint(
            "passport_valid_after_months IS NULL OR passport_valid_after_months BETWEEN 0 AND 24",
            name="passport_valid_after_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # ISO 3166-1 alpha-2.
    code: Mapped[str] = mapped_column(String(2), unique=True)
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    # Typical consultation length for this destination: about an hour for the UK,
    # 15–30 minutes for Schengen.
    consultation_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    # Passport rules for entry, checked in the consultation questionnaire. Schengen:
    # issued within the last 10 years and valid 3 months after the stay. NULL: no rule.
    passport_max_age_years: Mapped[int | None] = mapped_column(SmallInteger)
    passport_valid_after_months: Mapped[int | None] = mapped_column(SmallInteger)
    # The day it joined the Schengen area: from then on its days count towards 90/180
    # (app/core/stay.py). NULL: not in the area.
    schengen_since: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CountryRule(Base):
    """A workspace's direction: how it gets appointments for one pair «destination ×
    submission country»."""

    __tablename__ = "country_rules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "destination_id", "submission_id", name="uq_country_rules_pair"
        ),
        UniqueConstraint("id", "tenant_id", name="uq_country_rules_id_tenant"),
        CheckConstraint("destination_id <> submission_id", name="different_countries"),
        CheckConstraint(
            "(booking_type = 'windowed_application') = (window_days IS NOT NULL)",
            name="window_days_only_for_windowed",
        ),
        CheckConstraint(
            "window_days IS NULL OR window_days BETWEEN 1 AND 365", name="window_days_range"
        ),
        CheckConstraint(
            "appeal_deadline_days IS NULL "
            "OR (appeals_accepted AND appeal_deadline_days BETWEEN 1 AND 365)",
            name="appeal_deadline_needs_appeals",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT"), index=True
    )
    booking_type: Mapped[BookingType] = mapped_column(_enum(BookingType, "booking_type"))
    window_days: Mapped[int | None] = mapped_column(SmallInteger)
    # NULL: no operator, the embassy takes applications itself.
    vac_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vac_operators.id", ondelete="RESTRICT"), index=True
    )
    # «→ Germany»: the destination does not accept here; another country represents it.
    represented_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT")
    )
    identifier_type: Mapped[VacIdentifierType | None] = mapped_column(
        _enum(VacIdentifierType, "vac_identifier_type")
    )
    # Does the consulate in the submission country accept appeals? NULL: not known yet.
    appeals_accepted: Mapped[bool | None]
    appeal_deadline_days: Mapped[int | None] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    visa_centers: Mapped[list["VisaCenter"]] = relationship(
        order_by="VisaCenter.city_ru",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class VisaCenter(Base):
    """Where applications for a direction are submitted: each city has its own queue."""

    __tablename__ = "visa_centers"
    __table_args__ = (
        UniqueConstraint("rule_id", "city_ru", name="uq_visa_centers_rule_city"),
        UniqueConstraint("id", "tenant_id", name="uq_visa_centers_id_tenant"),
        ForeignKeyConstraint(
            ["rule_id", "tenant_id"],
            ["country_rules.id", "country_rules.tenant_id"],
            ondelete="CASCADE",
            name="fk_visa_centers_rule_id_same_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # The city as the specialist typed it; the English name is optional (forms fall back).
    city_ru: Mapped[str] = mapped_column(String(100))
    city_en: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

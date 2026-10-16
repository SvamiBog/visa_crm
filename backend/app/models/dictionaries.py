"""A specialist's own reference lists (справочники визовика).

Spec: docs/clients-module.md → «Справочники».

Every table carries tenant_id under Row-Level Security. Child rows point to their parent
through (parent_id, tenant_id), so a price or a term can never refer to another workspace's
registrar, partner or visa purpose. Entries are switched off rather than deleted: contacts
and cases will refer to them. Prices, terms and prepayments are copied into a case when it
is created, so their lists are replaced as a whole.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
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


class PartnerTermsType(StrEnum):
    """What the specialist pays a partner for a client (docs/decisions.md → «Деньги»)."""

    PERCENT = "percent"  # of the whole service price, no deductions
    FIXED = "fixed"
    NONE = "none"  # the partner is not paid
    UNSPECIFIED = "unspecified"  # not tracked: no payout is computed or stored


class RegistrarKind(StrEnum):
    COMPANY = "company"
    INDIVIDUAL = "individual"


class Hall(StrEnum):
    """Visa center hall: the registrar's fee depends on it."""

    STANDARD = "standard"
    PREMIUM = "premium"


def _tenant_fk(table: str, column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    """(column, tenant_id) → parent(id, tenant_id): the parent is always in the same workspace."""
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_{table}_{column}_same_tenant",
    )


class _NamedEntry:
    """Columns shared by the simple lists (visa purposes, lead channels)."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisaPurpose(_NamedEntry, Base):
    """Цель визы: tourism, guest, business, study… Each purpose has its own queue."""

    __tablename__ = "visa_purposes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_visa_purposes_tenant_name"),
        UniqueConstraint("id", "tenant_id", name="uq_visa_purposes_id_tenant"),
    )


class LeadChannel(_NamedEntry, Base):
    """Канал прихода: Instagram, word of mouth, partner, friends, ads."""

    __tablename__ = "lead_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_lead_channels_tenant_name"),
        UniqueConstraint("id", "tenant_id", name="uq_lead_channels_id_tenant"),
    )


class Partner(Base):
    """Someone who brings clients for a payout: a legalisation agent, a colleague."""

    __tablename__ = "partners"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_partners_id_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    contact: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    terms: Mapped[list["PartnerTerms"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )


class PartnerTerms(Base):
    """A partner's terms for one country; country_id NULL is the default for all others."""

    __tablename__ = "partner_terms"
    __table_args__ = (
        _tenant_fk("partner_terms", "partner_id", "partners", "CASCADE"),
        UniqueConstraint(
            "partner_id",
            "country_id",
            name="uq_partner_terms_partner_country",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(terms_type = 'percent') = (percent IS NOT NULL)", name="percent_only_for_percent"
        ),
        CheckConstraint(
            "(terms_type = 'fixed') = (amount IS NOT NULL AND currency IS NOT NULL)",
            name="amount_only_for_fixed",
        ),
        CheckConstraint(
            "percent IS NULL OR (percent > 0 AND percent <= 100)", name="percent_range"
        ),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(index=True)
    country_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT")
    )
    terms_type: Mapped[PartnerTermsType] = mapped_column(
        _enum(PartnerTermsType, "partner_terms_type")
    )
    percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))


class Registrar(Base):
    """A company or a person who puts clients in a visa center queue for a fee (талон)."""

    __tablename__ = "registrars"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_registrars_id_tenant"),
        CheckConstraint("low_balance IS NULL OR low_balance >= 0", name="low_balance_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[RegistrarKind] = mapped_column(_enum(RegistrarKind, "registrar_kind"))
    contact: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    # Settlements with the registrar are kept in this currency (app/core/registrar_ledger.py).
    currency: Mapped[str] = mapped_column(String(3))
    # Remind when the advance falls below this amount; NULL — no reminder.
    low_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prices: Mapped[list["RegistrarPrice"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )


class RegistrarPrice(Base):
    """The registrar's fee for one country, visa purpose (NULL: any) and hall."""

    __tablename__ = "registrar_prices"
    __table_args__ = (
        _tenant_fk("registrar_prices", "registrar_id", "registrars", "CASCADE"),
        _tenant_fk("registrar_prices", "purpose_id", "visa_purposes", "RESTRICT"),
        UniqueConstraint(
            "registrar_id",
            "country_id",
            "purpose_id",
            "hall",
            name="uq_registrar_prices_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    registrar_id: Mapped[uuid.UUID] = mapped_column(index=True)
    country_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("countries.id", ondelete="RESTRICT"))
    purpose_id: Mapped[uuid.UUID | None]
    hall: Mapped[Hall] = mapped_column(_enum(Hall, "visa_center_hall"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))


class Prepayment(Base):
    """Fixed prepayment for a country and visa purpose (NULL: any), in the base currency."""

    __tablename__ = "prepayments"
    __table_args__ = (
        _tenant_fk("prepayments", "purpose_id", "visa_purposes", "RESTRICT"),
        UniqueConstraint(
            "tenant_id",
            "country_id",
            "purpose_id",
            name="uq_prepayments_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    country_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("countries.id", ondelete="RESTRICT"))
    purpose_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

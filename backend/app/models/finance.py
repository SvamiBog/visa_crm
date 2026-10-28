"""Case finances (docs/clients-module.md → «Финансы»): the price, the talon and the partner
payout of a case, the client's payments and the specialist's own expenses.

Accounts are kept in the specialist's base currency (BYN for now); a payment or an
expense in another currency keeps its amount, currency, the rate and the amount in the
base currency (the rate is the National Bank's on that day, and can be corrected).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.dictionaries import PartnerTermsType


def _case_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["case_id", "tenant_id"],
        ["visa_cases.id", "visa_cases.tenant_id"],
        ondelete="CASCADE",
        name=f"fk_{table}_case_id_same_tenant",
    )


class ExchangeRate(Base):
    """The National Bank of Belarus rate for a day: BYN for one unit of the currency.
    Shared by all workspaces, filled from api.nbrb.by on first use (app/core/rates.py)."""

    __tablename__ = "exchange_rates"
    __table_args__ = (CheckConstraint("rate > 0", name="rate_positive"),)

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    on_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseFinance(Base):
    """The money of one case in the base currency; amounts not set yet are NULL."""

    __tablename__ = "case_finances"
    __table_args__ = (
        _case_fk("case_finances"),
        CheckConstraint("price IS NULL OR price >= 0", name="price_not_negative"),
        CheckConstraint("prepayment IS NULL OR prepayment > 0", name="prepayment_positive"),
        CheckConstraint("consultation_credit >= 0", name="credit_not_negative"),
        # The talon: all four together or none.
        CheckConstraint(
            "(registrar_fee_amount IS NULL) = (registrar_fee_currency IS NULL) "
            "AND (registrar_fee_amount IS NULL) = (registrar_fee_rate IS NULL) "
            "AND (registrar_fee_amount IS NULL) = (registrar_fee_base IS NULL)",
            name="registrar_fee_complete",
        ),
        # The day the talon was charged: set with the talon.
        CheckConstraint(
            "(registrar_fee_on IS NULL) = (registrar_fee_amount IS NULL)",
            name="registrar_fee_dated",
        ),
        CheckConstraint(
            "NOT registrar_fee_settled OR registrar_fee_amount IS NOT NULL",
            name="settled_with_talon",
        ),
        CheckConstraint(
            "(partner_percent IS NULL) OR partner_terms = 'percent'", name="percent_for_percent"
        ),
        CheckConstraint(
            "(partner_fixed IS NULL) OR partner_terms = 'fixed'", name="fixed_for_fixed"
        ),
        CheckConstraint(
            "partner_paid_on IS NULL OR partner_terms IN ('percent', 'fixed')",
            name="paid_only_when_paid_out",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # What the client was told, in the base currency.
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # The agreed prepayment (from the prepayments list by country and purpose).
    prepayment: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # The consultation fee counted towards the price.
    consultation_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), server_default=text("0"))
    # The talon: in the registrar's currency, its rate and the amount in the base currency.
    registrar_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    registrar_fee_currency: Mapped[str | None] = mapped_column(String(3))
    registrar_fee_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    registrar_fee_base: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # When the registrar put the client in the queue: the talon is charged that day.
    registrar_fee_on: Mapped[date | None] = mapped_column(Date)
    # Paid to the registrar outside the CRM's settlements (a talon from the specialist's
    # tables): in the profit, not in the registrar's balance.
    registrar_fee_settled: Mapped[bool] = mapped_column(server_default=text("false"))
    # The partner's terms copied into the case (changeable here); NULL — no partner.
    partner_terms: Mapped[PartnerTermsType | None] = mapped_column(
        Enum(
            PartnerTermsType,
            name="partner_terms_type",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        )
    )
    partner_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # A fixed payout in the base currency.
    partner_fixed: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    partner_paid_on: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class _Money:
    """Columns of a payment or an expense: the amount, its currency, the rate and the
    amount in the base currency."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    amount_base: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # A snapshot row of an export must not overwrite a later change (backlog 1.20).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _money_checks(table: str) -> tuple:
    return (
        _case_fk(table),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("rate > 0", name="rate_positive"),
    )


class CasePayment(_Money, Base):
    """A payment from the client; `official` — paid officially, so taxed (НПД)."""

    __tablename__ = "case_payments"
    __table_args__ = _money_checks("case_payments")

    paid_on: Mapped[date] = mapped_column(Date)
    official: Mapped[bool] = mapped_column(server_default=text("false"))
    note: Mapped[str | None] = mapped_column(String(200))


class CaseExpense(_Money, Base):
    """Something the specialist paid for the case themselves (not the client's flight)."""

    __tablename__ = "case_expenses"
    __table_args__ = _money_checks("case_expenses")

    spent_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(200))


class RegistrarPaymentMethod(StrEnum):
    CASH = "cash"
    TRANSFER = "transfer"


class RegistrarPayment(Base):
    """The specialist's payment to a registrar, in the registrar's currency: for some
    talons (`talons`) or in advance (NULL). Talons are charged from the case finances."""

    __tablename__ = "registrar_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["registrar_id", "tenant_id"],
            ["registrars.id", "registrars.tenant_id"],
            ondelete="RESTRICT",
            name="fk_registrar_payments_registrar_id_same_tenant",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("talons IS NULL OR talons > 0", name="talons_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    registrar_id: Mapped[uuid.UUID] = mapped_column(index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    method: Mapped[RegistrarPaymentMethod] = mapped_column(
        Enum(
            RegistrarPaymentMethod,
            name="registrar_payment_method",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    talons: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

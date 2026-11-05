"""The specialists' subscription to the CRM (roadmap 3.8, decisions.md → «Слоты и SaaS»):
tariffs shared by the whole platform and the payments the admin marks for a workspace.
The subscription itself — the tariff, its start and the trial — is kept on the tenant.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tariff(Base):
    """A monthly price the same for every specialist on it; only the admin writes it."""

    __tablename__ = "tariffs"
    __table_args__ = (CheckConstraint("price > 0", name="price_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    # An inactive tariff is not given to anyone new; specialists already on it keep it.
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubscriptionPayment(Base):
    """A subscription payment received outside the system, as the admin marked it."""

    __tablename__ = "subscription_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("months BETWEEN 1 AND 24", name="months_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    paid_on: Mapped[date] = mapped_column(Date)
    # How many months of the subscription the payment covers.
    months: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

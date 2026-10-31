import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tenant(Base):
    """A specialist's workspace (визовик = арендатор). All business data belongs to one tenant."""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "(tariff_id IS NULL) = (subscription_starts_on IS NULL)",
            name="subscription_starts_with_tariff",
        ),
        CheckConstraint(
            "trial_until IS NULL OR trial_until > subscription_starts_on",
            name="trial_after_start",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    base_currency: Mapped[str] = mapped_column(String(3), server_default="BYN")
    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/Minsk")
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    # Currencies the specialist accepts payments in; the base currency is always among them.
    payment_currencies: Mapped[list[str]] = mapped_column(
        ARRAY(String(3)), server_default=text("'{BYN,PLN,USD,EUR}'")
    )
    # Tax on officially paid payments, percent (НПД in Belarus: 10).
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default=text("0"))
    # Modules the admin turned on (keys from app/core/modules.py).
    modules: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'"))
    # The tenant's vault data key, encrypted with the master key (app/core/vault.py).
    vault_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    # The specialist's reminder windows over the defaults (app/core/reminders.py: Settings).
    reminder_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    # The CRM subscription (app/core/subscription.py); no tariff — no subscription.
    # Only the admin changes these three.
    tariff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT")
    )
    subscription_starts_on: Mapped[date | None] = mapped_column(Date)
    trial_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

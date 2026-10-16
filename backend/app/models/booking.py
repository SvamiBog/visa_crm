"""Bookings for the document package (брони; docs/clients-module.md → «Брони для пакета
документов»): hotels with their free cancellation deadline and the client's flight
booking with its validity. Kept per case; the system watches the deadlines until the
case reaches its result (app/core/bookings.py).
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
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_bookings_{column}_same_tenant",
    )


class BookingKind(StrEnum):
    HOTEL = "hotel"
    # Made and paid by the client; the specialist only records it.
    FLIGHT = "flight"


class BookingStatus(StrEnum):
    ACTIVE = "active"
    RESCHEDULED = "rescheduled"  # moved to other dates: still to be watched
    CANCELLED = "cancelled"


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        _same_tenant("case_id", "visa_cases", "CASCADE"),
        _same_tenant("vault_entry_id", "vault_entries", "SET NULL (vault_entry_id)"),
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="dates_order"),
        # A hotel has a free cancellation date, a flight booking a validity date.
        CheckConstraint(
            "kind = 'hotel' OR free_cancel_until IS NULL", name="cancel_date_for_hotel"
        ),
        CheckConstraint("kind = 'flight' OR valid_until IS NULL", name="validity_for_flight"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[BookingKind] = mapped_column(_enum(BookingKind, "booking_kind"))
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus, "booking_status"), server_default=BookingStatus.ACTIVE.value
    )
    # The hotel and its city, or the flight.
    place: Mapped[str] = mapped_column(String(200))
    # Stay (check-in, check-out) or flight dates (there, back).
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    # The booking number: to find it quickly in the account.
    reference: Mapped[str | None] = mapped_column(String(64))
    # The main field: the last day it can be cancelled for free.
    free_cancel_until: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    # The account the booking was made in (usually the specialist's Booking account).
    vault_entry_id: Mapped[uuid.UUID | None]
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

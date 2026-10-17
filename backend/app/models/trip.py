"""Trips of a client (поездки): the history the 90/180 calculator counts (backlog 2.6,
app/core/stay.py). Kept on the contact, since a person's days in the Schengen area do
not depend on a case. Personal data: dates and countries stay out of audit details.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            ondelete="CASCADE",
            name="fk_trips_contact_id_same_tenant",
        ),
        Index("ix_trips_contact_entered", "contact_id", "entered_on"),
        CheckConstraint("left_on IS NULL OR left_on >= entered_on", name="dates_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID]
    # Where they were: whether the days count comes from the country's schengen_since.
    country_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT"), index=True
    )
    entered_on: Mapped[date] = mapped_column(Date)
    # None: still there.
    left_on: Mapped[date | None] = mapped_column(Date)
    # Under a long-stay visa (D) or a residence permit: the days do not count.
    long_stay: Mapped[bool] = mapped_column(server_default=text("false"))
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

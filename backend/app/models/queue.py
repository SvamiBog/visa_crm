"""Getting an appointment for a case (docs/clients-module.md → «Очереди и попытки записи»).

Two kinds, set by the country rule: a waiting list (лист ожидания), where a registrar
puts the client in the visa center's queue through their own CRM, and a windowed
application (заявка с окном, France: 60 days), sent again until a date comes. The
system keeps the record — who waits since when, through whom and under what number —
and sends nothing to the visa center.
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
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _case_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["case_id", "tenant_id"],
        ["visa_cases.id", "visa_cases.tenant_id"],
        ondelete="CASCADE",
        name=f"fk_{table}_case_id_same_tenant",
    )


class WaitingListEntry(Base):
    """A case in a visa center's waiting list. Its status is derived from the dates
    (app/core/queues.py); taken off the list, it stays as history and a new entry can
    start."""

    __tablename__ = "waiting_list_entries"
    __table_args__ = (
        _case_fk("waiting_list_entries"),
        # One open entry per case.
        Index(
            "uq_waiting_list_entries_open_case",
            "case_id",
            unique=True,
            postgresql_where=text("removed_on IS NULL"),
        ),
        CheckConstraint("letter_on IS NULL OR letter_on >= queued_on", name="letter_after_queued"),
        CheckConstraint(
            "removed_on IS NULL OR removed_on >= queued_on", name="removed_after_queued"
        ),
        CheckConstraint(
            "removed_reason IS NULL OR removed_on IS NOT NULL", name="reason_only_when_removed"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Put in the queue: the wait is counted from here.
    queued_on: Mapped[date] = mapped_column(Date)
    # The letter with the appointment came to the client's account.
    letter_on: Mapped[date | None] = mapped_column(Date)
    # Taken off the list: the client did not wait.
    removed_on: Mapped[date | None] = mapped_column(Date)
    removed_reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttemptOutcome(StrEnum):
    """How an application attempt ended. NULL: still open (or past its deadline)."""

    DATE_RECEIVED = "date_received"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApplicationAttempt(Base):
    """One application with a window (France: 60 days); sent again as many times as the
    client wants (colleagues see up to 6)."""

    __tablename__ = "application_attempts"
    __table_args__ = (
        _case_fk("application_attempts"),
        UniqueConstraint("case_id", "number", name="uq_application_attempts_case_number"),
        # One open attempt per case.
        Index(
            "uq_application_attempts_open_case",
            "case_id",
            unique=True,
            postgresql_where=text("outcome IS NULL"),
        ),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("deadline_on >= sent_on", name="deadline_after_sent"),
        CheckConstraint("closed_on IS NULL OR outcome IS NOT NULL", name="closed_with_outcome"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[int] = mapped_column(SmallInteger)
    sent_on: Mapped[date] = mapped_column(Date)
    # sent_on + the window from the country rule when the attempt was sent.
    deadline_on: Mapped[date] = mapped_column(Date)
    outcome: Mapped[AttemptOutcome | None] = mapped_column(
        Enum(
            AttemptOutcome,
            name="attempt_outcome",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    closed_on: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

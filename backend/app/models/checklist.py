"""Document checklists (чек-листы документов, backlog 2.2): what to collect for a visa to a
country.

Shared items are kept by the admin and have no tenant_id, like the countries reference:
every scope reads them, only the system scope writes (migration 0026). A specialist adds
her own items on top, seen in her workspace only. An item may be for one submission
country and for some purpose kinds only; app/core/checklists.py decides which items a
case gets. Items are switched off, never deleted: case checklists (backlog 2.3) will
refer to them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.dictionaries import PurposeKind


def _checks() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(
            "max_age_days IS NULL OR max_age_days BETWEEN 1 AND 365", name="max_age_days_range"
        ),
    )


class _Item:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The visa's country (страна визы).
    country_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("countries.id", ondelete="RESTRICT"))
    # Only when applying in this country (a residence permit in Turkey); NULL: anywhere.
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT")
    )
    # Only for visa purposes of these kinds; empty: for any purpose.
    purposes: Mapped[list[PurposeKind]] = mapped_column(
        ARRAY(
            Enum(
                PurposeKind,
                name="purpose_kind",
                values_callable=lambda e: [m.value for m in e],
                create_type=False,
            )
        ),
        server_default=text("'{}'"),
    )
    name: Mapped[str] = mapped_column(String(200))
    # What the document must be like, or when it is needed («for the employed»).
    note: Mapped[str | None] = mapped_column(String(1000))
    # False: depends on the applicant (по ситуации).
    required: Mapped[bool] = mapped_column(server_default=text("true"))
    # Issued at most that many days before the submission day; NULL: no limit.
    max_age_days: Mapped[int | None] = mapped_column(SmallInteger)
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChecklistItem(_Item, Base):
    """An item of the shared checklist: the admin keeps it for every workspace."""

    __tablename__ = "checklist_items"
    __table_args__ = (*_checks(), Index("ix_checklist_items_country_id", "country_id"))


class OwnChecklistItem(_Item, Base):
    """A specialist's own item, on top of the shared ones."""

    __tablename__ = "own_checklist_items"
    __table_args__ = (
        *_checks(),
        UniqueConstraint("id", "tenant_id", name="uq_own_checklist_items_id_tenant"),
        Index("ix_own_checklist_items_tenant_country", "tenant_id", "country_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))

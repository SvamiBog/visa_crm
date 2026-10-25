"""The package of a case (backlog 2.3): what the specialist marked for each item of the
case's document checklist. The status of an item at the submission date is computed,
not stored (app/core/package.py).
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PackageItemState(StrEnum):
    PRESENT = "present"  # есть
    MISSING = "missing"  # нет
    NOT_NEEDED = "not_needed"  # не нужно


def _same_tenant(column: str, parent: str, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        ondelete=ondelete,
        name=f"fk_package_items_{column}_same_tenant",
    )


class PackageItem(Base):
    """One checklist item in a case: a shared item or one of the specialist's own. Items
    are switched off, never deleted, so a mark outlives its item leaving the list."""

    __tablename__ = "package_items"
    __table_args__ = (
        _same_tenant("case_id", "visa_cases", "CASCADE"),
        _same_tenant("own_item_id", "own_checklist_items", "RESTRICT"),
        CheckConstraint("(checklist_item_id IS NULL) <> (own_item_id IS NULL)", name="one_item"),
        CheckConstraint("state = 'present' OR document_date IS NULL", name="date_when_present"),
        UniqueConstraint("case_id", "checklist_item_id", name="uq_package_items_case_shared"),
        UniqueConstraint("case_id", "own_item_id", name="uq_package_items_case_own"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID]
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="RESTRICT")
    )
    own_item_id: Mapped[uuid.UUID | None]
    state: Mapped[PackageItemState] = mapped_column(
        Enum(
            PackageItemState,
            name="package_item_state",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # The date on the document: freshness is counted from it.
    document_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

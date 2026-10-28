"""Import of the specialist's spreadsheets (backlog 1.14): the remembered column mapping,
the runs with their reports and which records each spreadsheet row became, so a repeated
import updates them instead of adding duplicates.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ImportKind(StrEnum):
    """What a spreadsheet becomes (docs/clients-module.md → «Импорт из текущих таблиц»)."""

    CONSULTATIONS = "consultations"  # contacts and their consultations
    CASES = "cases"  # «Клиенты в работе»: contacts and cases, accounts to the vault
    QUEUE = "queue"  # Poland, Latvia, Italy: waiting list entries
    ATTEMPTS = "attempts"  # France: windowed applications
    BOOKINGS = "bookings"  # Booking: hotel bookings of cases
    # The VisaCRM data format (backlog 1.20): one workbook with every sheet. The kinds
    # above are the old column-mapping import (1.14, 1.16), kept for its run history.
    WORKBOOK = "workbook"


def _kind_enum() -> Enum:
    return Enum(ImportKind, name="import_kind", values_callable=lambda e: [m.value for m in e])


class ImportMapping(Base):
    """The last mapping of a kind in the old column-mapping import (1.14, 1.16). No longer
    used since the VisaCRM data format (1.20); dropped by a later migration."""

    __tablename__ = "import_mappings"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", name="uq_import_mappings_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ImportKind] = mapped_column(_kind_enum())
    columns: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportRun(Base):
    """One import with its report: counts, skipped rows and warnings by row number and
    column header — no cell values."""

    __tablename__ = "import_runs"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_import_runs_id_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ImportKind] = mapped_column(_kind_enum())
    file_name: Mapped[str] = mapped_column(String(200))
    sheet: Mapped[str | None] = mapped_column(String(100))
    rows: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportRow(Base):
    """A spreadsheet row seen before: its key (a hash of the kind, the person and what
    tells their rows apart) and the ids of the records it became."""

    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "key", name="uq_import_rows_key"),
        ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["import_runs.id", "import_runs.tenant_id"],
            ondelete="CASCADE",
            name="fk_import_rows_run_id_same_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ImportKind] = mapped_column(_kind_enum())
    key: Mapped[str] = mapped_column(String(64))
    # The last run that wrote the row.
    run_id: Mapped[uuid.UUID] = mapped_column(index=True)
    entities: Mapped[dict[str, str]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

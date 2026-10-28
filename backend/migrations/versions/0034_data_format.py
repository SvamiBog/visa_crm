"""import of the VisaCRM data format: a new import kind and when rows last changed

Backlog 1.20 (docs/data-format.md, docs/decisions.md → «Формат данных VisaCRM»): the
import takes one workbook with every sheet at once, so its runs and remembered rows get
their own kind, «workbook». A row exported from the CRM and loaded back is a snapshot of
its record; a record changed in the CRM after the export must not be overwritten by it,
so the queue entries, windowed applications, payments and expenses get updated_at like
the other records already have. Existing rows count as changed now.

The remembered column mappings of the old import (import_mappings) are no longer used;
the table is dropped by a later revision, once no running code reads it.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("waiting_list_entries", "application_attempts", "case_payments", "case_expenses")
OLD_KINDS = ("consultations", "cases", "queue", "attempts", "bookings")


def upgrade() -> None:
    op.execute("ALTER TYPE import_kind ADD VALUE IF NOT EXISTS 'workbook'")
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Drops updated_at of the four tables and the «workbook» kind with its runs and
    remembered rows: a later import of the same file creates its records again."""
    for table in TABLES:
        op.drop_column(table, "updated_at")
    op.execute("DELETE FROM import_rows WHERE kind = 'workbook'")
    op.execute("DELETE FROM import_runs WHERE kind = 'workbook'")
    # Postgres cannot drop an enum value: the type is made again without it.
    op.execute("ALTER TYPE import_kind RENAME TO import_kind_old")
    kinds = ", ".join(f"'{kind}'" for kind in OLD_KINDS)
    op.execute(f"CREATE TYPE import_kind AS ENUM ({kinds})")
    for table in ("import_mappings", "import_runs", "import_rows"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN kind TYPE import_kind USING kind::text::import_kind"
        )
    op.execute("DROP TYPE import_kind_old")

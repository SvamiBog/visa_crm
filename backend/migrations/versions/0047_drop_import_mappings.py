"""the remembered column mappings of the old import go: import_mappings is dropped

The import with column mapping (backlog 1.14, 1.16) was replaced by the VisaCRM data format
(1.21, migration 0035): no code has read or written import_mappings since. The decision of
2026-10-28 (docs/decisions.md → «Импорт формата VisaCRM, первая часть») left the table for
the second part of 1.21, once no running instance used it. The runs and the remembered rows
of old imports (import_runs, import_rows) stay: the history shows them.

Upgrade drops the table with its rows, index and both policies (tenant_isolation, and
employee_scope from 0031). The mappings are lost; nothing reads them.

Downgrade brings the table back as 0023 and 0031 made it, with the same keys, index and
policies, but empty.

Deploy: the previous version names the table only to empty a workspace (app/core/erase.py
goes through every model). «Удаление всех данных» asked on it in the moments between this
migration and the new version taking the traffic answers 500 and works when asked again.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The type stays: import_runs and import_rows still use it.
KIND = postgresql.ENUM(name="import_kind", create_type=False)


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # The policies and the index go with the table.
    op.drop_table("import_mappings")


def downgrade() -> None:
    jsonb_empty = sa.text("'{}'::jsonb")
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("columns", postgresql.JSONB(), server_default=jsonb_empty, nullable=False),
        sa.Column("options", postgresql.JSONB(), server_default=jsonb_empty, nullable=False),
        sa.Column("values", postgresql.JSONB(), server_default=jsonb_empty, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_import_mappings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_mappings")),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_import_mappings_kind"),
    )
    op.create_index(op.f("ix_import_mappings_tenant_id"), "import_mappings", ["tenant_id"])
    enable_tenant_rls("import_mappings")
    # The specialist's own, as 0031 made it: an employee sees none of it.
    op.execute(
        "CREATE POLICY employee_scope ON import_mappings AS RESTRICTIVE "
        "USING (app_employee_id() IS NULL OR (false)) "
        "WITH CHECK (app_employee_id() IS NULL OR (false))"
    )

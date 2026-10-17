"""spreadsheet import: remembered mappings, runs with reports, imported rows

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = postgresql.ENUM(
    "consultations", "cases", "queue", "attempts", "bookings", name="import_kind", create_type=False
)


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def _tenant(table: str) -> tuple[sa.Column, sa.ForeignKeyConstraint]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False), sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    KIND.create(op.get_bind())
    jsonb_empty = sa.text("'{}'::jsonb")
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant("import_mappings"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_mappings")),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_import_mappings_kind"),
    )
    op.create_index(op.f("ix_import_mappings_tenant_id"), "import_mappings", ["tenant_id"])

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant("import_runs"),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("file_name", sa.String(length=200), nullable=False),
        sa.Column("sheet", sa.String(length=100), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_import_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_runs")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_import_runs_id_tenant"),
    )
    op.create_index(op.f("ix_import_runs_tenant_id"), "import_runs", ["tenant_id"])

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_tenant("import_rows"),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("entities", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["import_runs.id", "import_runs.tenant_id"],
            name="fk_import_rows_run_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_rows")),
        sa.UniqueConstraint("tenant_id", "kind", "key", name="uq_import_rows_key"),
    )
    op.create_index(op.f("ix_import_rows_run_id"), "import_rows", ["run_id"])
    op.create_index(op.f("ix_import_rows_tenant_id"), "import_rows", ["tenant_id"])

    for table in ("import_mappings", "import_runs", "import_rows"):
        enable_tenant_rls(table)


def downgrade() -> None:
    op.drop_table("import_rows")
    op.drop_table("import_runs")
    op.drop_table("import_mappings")
    KIND.drop(op.get_bind())

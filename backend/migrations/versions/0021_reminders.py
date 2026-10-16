"""in-CRM reminders: dismissed occurrences and the specialist's reminder windows

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "reminder_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "reminder_dismissals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("dismissed_by", sa.Uuid(), nullable=True),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dismissed_by"],
            ["users.id"],
            name=op.f("fk_reminder_dismissals_dismissed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_reminder_dismissals_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_dismissals")),
        sa.UniqueConstraint("tenant_id", "key", name="uq_reminder_dismissals_key"),
    )
    op.create_index(op.f("ix_reminder_dismissals_tenant_id"), "reminder_dismissals", ["tenant_id"])
    enable_tenant_rls("reminder_dismissals")


def downgrade() -> None:
    op.drop_table("reminder_dismissals")
    op.drop_column("tenants", "reminder_settings")

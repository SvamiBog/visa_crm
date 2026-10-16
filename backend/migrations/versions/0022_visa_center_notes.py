"""the specialist's remarks about visa centers (analytics)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
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
    op.create_table(
        "visa_center_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("visa_center_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_visa_center_notes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_visa_center_notes_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["visa_center_id"],
            ["visa_centers.id"],
            name=op.f("fk_visa_center_notes_visa_center_id_visa_centers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visa_center_notes")),
        sa.UniqueConstraint("tenant_id", "visa_center_id", name="uq_visa_center_notes_center"),
    )
    op.create_index(op.f("ix_visa_center_notes_tenant_id"), "visa_center_notes", ["tenant_id"])
    enable_tenant_rls("visa_center_notes")


def downgrade() -> None:
    op.drop_table("visa_center_notes")

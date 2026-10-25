"""package of a case: what the specialist marked for each item of its document checklist

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATE = postgresql.ENUM("present", "missing", "not_needed", name="package_item_state")


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
        "package_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("checklist_item_id", sa.Uuid(), nullable=True),
        sa.Column("own_item_id", sa.Uuid(), nullable=True),
        sa.Column("state", STATE, nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(checklist_item_id IS NULL) <> (own_item_id IS NULL)",
            name=op.f("ck_package_items_one_item"),
        ),
        sa.CheckConstraint(
            "state = 'present' OR document_date IS NULL",
            name=op.f("ck_package_items_date_when_present"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name="fk_package_items_case_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["own_item_id", "tenant_id"],
            ["own_checklist_items.id", "own_checklist_items.tenant_id"],
            name="fk_package_items_own_item_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"],
            ["checklist_items.id"],
            name=op.f("fk_package_items_checklist_item_id_checklist_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_package_items_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_items")),
        sa.UniqueConstraint("case_id", "checklist_item_id", name="uq_package_items_case_shared"),
        sa.UniqueConstraint("case_id", "own_item_id", name="uq_package_items_case_own"),
    )
    op.create_index(op.f("ix_package_items_tenant_id"), "package_items", ["tenant_id"])
    enable_tenant_rls("package_items")


def downgrade() -> None:
    op.drop_table("package_items")
    STATE.drop(op.get_bind())

"""bookings of a case: hotels with their free cancellation date and the client's flight
booking with its validity

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = postgresql.ENUM("hotel", "flight", name="booking_kind")
STATUS = postgresql.ENUM("active", "rescheduled", "cancelled", name="booking_status")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # Bookings refer to the account they were made in within the same workspace.
    op.create_unique_constraint("uq_vault_entries_id_tenant", "vault_entries", ["id", "tenant_id"])
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", KIND, nullable=False),
        sa.Column("status", STATUS, server_default="active", nullable=False),
        sa.Column("place", sa.String(length=200), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column("free_cancel_until", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("vault_entry_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            "ends_on IS NULL OR ends_on >= starts_on", name=op.f("ck_bookings_dates_order")
        ),
        sa.CheckConstraint(
            "kind = 'hotel' OR free_cancel_until IS NULL",
            name=op.f("ck_bookings_cancel_date_for_hotel"),
        ),
        sa.CheckConstraint(
            "kind = 'flight' OR valid_until IS NULL",
            name=op.f("ck_bookings_validity_for_flight"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name="fk_bookings_case_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vault_entry_id", "tenant_id"],
            ["vault_entries.id", "vault_entries.tenant_id"],
            name="fk_bookings_vault_entry_id_same_tenant",
            ondelete="SET NULL (vault_entry_id)",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_bookings_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_bookings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(op.f("ix_bookings_case_id"), "bookings", ["case_id"])
    op.create_index(op.f("ix_bookings_tenant_id"), "bookings", ["tenant_id"])
    enable_tenant_rls("bookings")


def downgrade() -> None:
    op.drop_table("bookings")
    STATUS.drop(op.get_bind())
    KIND.drop(op.get_bind())
    op.drop_constraint("uq_vault_entries_id_tenant", "vault_entries", type_="unique")

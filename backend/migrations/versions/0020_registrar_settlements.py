"""registrar settlements: the registrar's currency and low-balance threshold, the day a
talon is charged, payments to registrars

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METHOD = sa.Enum("cash", "transfer", name="registrar_payment_method")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # The currency of a registrar's settlements: the one most of their prices are in,
    # else the workspace's base currency.
    op.add_column("registrars", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(
        "registrars", sa.Column("low_balance", sa.Numeric(precision=14, scale=2), nullable=True)
    )
    op.execute(
        """
        UPDATE registrars r SET currency = coalesce(
            (SELECT p.currency FROM registrar_prices p WHERE p.registrar_id = r.id
             GROUP BY p.currency ORDER BY count(*) DESC, p.currency LIMIT 1),
            (SELECT t.base_currency FROM tenants t WHERE t.id = r.tenant_id))
        """
    )
    op.alter_column("registrars", "currency", nullable=False)
    op.create_check_constraint(
        op.f("ck_registrars_low_balance_not_negative"),
        "registrars",
        "low_balance IS NULL OR low_balance >= 0",
    )

    # A talon already set is charged the day the client was queued, else the day the
    # case finances were last changed.
    op.add_column("case_finances", sa.Column("registrar_fee_on", sa.Date(), nullable=True))
    op.execute(
        """
        UPDATE case_finances f SET registrar_fee_on = coalesce(
            (SELECT min(w.queued_on) FROM waiting_list_entries w WHERE w.case_id = f.case_id),
            (f.updated_at AT TIME ZONE 'UTC')::date)
        WHERE f.registrar_fee_amount IS NOT NULL
        """
    )
    op.create_check_constraint(
        op.f("ck_case_finances_registrar_fee_dated"),
        "case_finances",
        "(registrar_fee_on IS NULL) = (registrar_fee_amount IS NULL)",
    )

    op.create_table(
        "registrar_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("registrar_id", sa.Uuid(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("method", METHOD, nullable=False),
        sa.Column("talons", sa.SmallInteger(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_registrar_payments_amount_positive")),
        sa.CheckConstraint(
            "talons IS NULL OR talons > 0", name=op.f("ck_registrar_payments_talons_positive")
        ),
        sa.ForeignKeyConstraint(
            ["registrar_id", "tenant_id"],
            ["registrars.id", "registrars.tenant_id"],
            name="fk_registrar_payments_registrar_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_registrar_payments_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_registrar_payments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registrar_payments")),
    )
    op.create_index(
        op.f("ix_registrar_payments_registrar_id"), "registrar_payments", ["registrar_id"]
    )
    op.create_index(op.f("ix_registrar_payments_tenant_id"), "registrar_payments", ["tenant_id"])
    enable_tenant_rls("registrar_payments")


def downgrade() -> None:
    op.drop_table("registrar_payments")
    METHOD.drop(op.get_bind(), checkfirst=True)
    op.drop_constraint(op.f("ck_case_finances_registrar_fee_dated"), "case_finances", type_="check")
    op.drop_column("case_finances", "registrar_fee_on")
    op.drop_constraint(op.f("ck_registrars_low_balance_not_negative"), "registrars", type_="check")
    op.drop_column("registrars", "low_balance")
    op.drop_column("registrars", "currency")

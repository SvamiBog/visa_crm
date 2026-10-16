"""case finances: price, talon, partner terms, payments and expenses; the National Bank's
rates; the «finances» module

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTNER_TERMS = postgresql.ENUM(name="partner_terms_type", create_type=False)
ANY_SCOPE = (
    "current_setting('app.system', true) = 'on' "
    "OR nullif(current_setting('app.tenant_id', true), '') IS NOT NULL"
)
SYSTEM = "current_setting('app.system', true) = 'on'"


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def enable_shared_rls(table: str) -> None:
    """Shared reference: every scope reads it, only the system scope writes."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read_any_scope ON {table} FOR SELECT USING ({ANY_SCOPE})")
    for command in ("INSERT", "UPDATE", "DELETE"):
        using = "" if command == "INSERT" else f" USING ({SYSTEM})"
        check = "" if command == "DELETE" else f" WITH CHECK ({SYSTEM})"
        op.execute(f"CREATE POLICY system_{command.lower()} ON {table} FOR {command}{using}{check}")


def case_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["case_id", "tenant_id"],
        ["visa_cases.id", "visa_cases.tenant_id"],
        name=f"fk_{table}_case_id_same_tenant",
        ondelete="CASCADE",
    )


def tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="CASCADE",
    )


def money_table(table: str, *columns: sa.Column) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("amount_base", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *columns,
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{table}_amount_positive")),
        sa.CheckConstraint("rate > 0", name=op.f(f"ck_{table}_rate_positive")),
        case_fk(table),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f(f"fk_{table}_created_by_users"),
            ondelete="SET NULL",
        ),
        tenant_fk(table),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    )
    op.create_index(op.f(f"ix_{table}_case_id"), table, ["case_id"])
    op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"])
    enable_tenant_rls(table)


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rate > 0", name=op.f("ck_exchange_rates_rate_positive")),
        sa.PrimaryKeyConstraint("currency", "on_date", name=op.f("pk_exchange_rates")),
    )
    enable_shared_rls("exchange_rates")

    op.create_table(
        "case_finances",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("prepayment", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "consultation_credit",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("registrar_fee_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("registrar_fee_currency", sa.String(length=3), nullable=True),
        sa.Column("registrar_fee_rate", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("registrar_fee_base", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("partner_terms", PARTNER_TERMS, nullable=True),
        sa.Column("partner_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("partner_fixed", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("partner_paid_on", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price IS NULL OR price >= 0", name=op.f("ck_case_finances_price_not_negative")
        ),
        sa.CheckConstraint(
            "prepayment IS NULL OR prepayment > 0",
            name=op.f("ck_case_finances_prepayment_positive"),
        ),
        sa.CheckConstraint(
            "consultation_credit >= 0", name=op.f("ck_case_finances_credit_not_negative")
        ),
        sa.CheckConstraint(
            "(registrar_fee_amount IS NULL) = (registrar_fee_currency IS NULL) "
            "AND (registrar_fee_amount IS NULL) = (registrar_fee_rate IS NULL) "
            "AND (registrar_fee_amount IS NULL) = (registrar_fee_base IS NULL)",
            name=op.f("ck_case_finances_registrar_fee_complete"),
        ),
        sa.CheckConstraint(
            "(partner_percent IS NULL) OR partner_terms = 'percent'",
            name=op.f("ck_case_finances_percent_for_percent"),
        ),
        sa.CheckConstraint(
            "(partner_fixed IS NULL) OR partner_terms = 'fixed'",
            name=op.f("ck_case_finances_fixed_for_fixed"),
        ),
        sa.CheckConstraint(
            "partner_paid_on IS NULL OR partner_terms IN ('percent', 'fixed')",
            name=op.f("ck_case_finances_paid_only_when_paid_out"),
        ),
        case_fk("case_finances"),
        tenant_fk("case_finances"),
        sa.PrimaryKeyConstraint("case_id", name=op.f("pk_case_finances")),
    )
    op.create_index(op.f("ix_case_finances_tenant_id"), "case_finances", ["tenant_id"])
    enable_tenant_rls("case_finances")

    money_table(
        "case_payments",
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("official", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
    )
    money_table(
        "case_expenses",
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
    )

    # Case finances are part of the core CRM: workspaces that keep cases get them.
    # Employees get them only when their specialist gives them.
    op.execute(
        "UPDATE tenants SET modules = array_append(modules, 'finances') "
        "WHERE 'cases' = ANY(modules) AND NOT 'finances' = ANY(modules)"
    )


def downgrade() -> None:
    op.execute("UPDATE users SET modules = array_remove(modules, 'finances')")
    op.execute("UPDATE tenants SET modules = array_remove(modules, 'finances')")
    op.drop_table("case_expenses")
    op.drop_table("case_payments")
    op.drop_table("case_finances")
    op.drop_table("exchange_rates")

"""specialists' subscription to the CRM: tariffs, the subscription of a workspace, payments

Roadmap 3.8 (docs/decisions.md → «Слоты и SaaS», 2026-10-30): tariffs are the same for
every specialist and paid monthly, so they are shared platform data — every scope reads
them, only the system scope (admin) writes. The subscription of a workspace is its
tariff, the start and an optional trial end, kept on the tenant; a workspace without a
tariff has no subscription, so existing workspaces need no backfill.

Payments are received outside the system and marked by the admin. A specialist sees
their own payments (tenant_isolation), but even their own scope cannot write one: a
restrictive policy lets only the system scope insert, change or delete.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM = "current_setting('app.system', true) = 'on'"
# Any scope: the admin panel (system) or a workspace. A session without a scope sees nothing.
ANY_SCOPE = f"{SYSTEM} OR nullif(current_setting('app.tenant_id', true), '') IS NOT NULL"


def enable_shared_rls(table: str) -> None:
    """Shared reference: every scope reads it, only the system scope (admin) writes."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read_any_scope ON {table} FOR SELECT USING ({ANY_SCOPE})")
    for command in ("INSERT", "UPDATE", "DELETE"):
        using = "" if command == "INSERT" else f" USING ({SYSTEM})"
        check = "" if command == "DELETE" else f" WITH CHECK ({SYSTEM})"
        op.execute(f"CREATE POLICY system_{command.lower()} ON {table} FOR {command}{using}{check}")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = f"{SYSTEM} OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def system_writes_only(table: str) -> None:
    """On top of tenant_isolation: the workspace reads its rows, only the system writes."""
    for command in ("INSERT", "UPDATE", "DELETE"):
        using = "" if command == "INSERT" else f" USING ({SYSTEM})"
        check = "" if command == "DELETE" else f" WITH CHECK ({SYSTEM})"
        op.execute(
            f"CREATE POLICY system_{command.lower()} ON {table} AS RESTRICTIVE "
            f"FOR {command}{using}{check}"
        )


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price > 0", name=op.f("ck_tariffs_price_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
        sa.UniqueConstraint("name", name=op.f("uq_tariffs_name")),
    )
    enable_shared_rls("tariffs")

    op.add_column("tenants", sa.Column("tariff_id", sa.Uuid(), nullable=True))
    op.add_column("tenants", sa.Column("subscription_starts_on", sa.Date(), nullable=True))
    op.add_column("tenants", sa.Column("trial_until", sa.Date(), nullable=True))
    op.create_foreign_key(
        op.f("fk_tenants_tariff_id_tariffs"),
        "tenants",
        "tariffs",
        ["tariff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_tenants_subscription_starts_with_tariff"),
        "tenants",
        "(tariff_id IS NULL) = (subscription_starts_on IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_tenants_trial_after_start"),
        "tenants",
        "trial_until IS NULL OR trial_until > subscription_starts_on",
    )

    op.create_table(
        "subscription_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("months", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_subscription_payments_amount_positive")),
        sa.CheckConstraint(
            "months BETWEEN 1 AND 24", name=op.f("ck_subscription_payments_months_range")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_subscription_payments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_payments")),
    )
    op.create_index(
        op.f("ix_subscription_payments_tenant_id"), "subscription_payments", ["tenant_id"]
    )
    enable_tenant_rls("subscription_payments")
    system_writes_only("subscription_payments")


def downgrade() -> None:
    """Drops the tariffs, the subscriptions and every payment marked: they are lost."""
    op.drop_table("subscription_payments")
    op.drop_constraint(op.f("ck_tenants_trial_after_start"), "tenants", type_="check")
    op.drop_constraint(op.f("ck_tenants_subscription_starts_with_tariff"), "tenants", type_="check")
    op.drop_constraint(op.f("fk_tenants_tariff_id_tariffs"), "tenants", type_="foreignkey")
    op.drop_column("tenants", "trial_until")
    op.drop_column("tenants", "subscription_starts_on")
    op.drop_column("tenants", "tariff_id")
    op.drop_table("tariffs")

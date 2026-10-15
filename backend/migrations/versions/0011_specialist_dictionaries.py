"""a specialist's own lists: visa purposes, lead channels, partners with terms,
registrars with prices, prepayments; payment currencies of a workspace

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "visa_purposes",
    "lead_channels",
    "partners",
    "partner_terms",
    "registrars",
    "registrar_prices",
    "prepayments",
)
ENUMS = ("partner_terms_type", "registrar_kind", "visa_center_hall")
# Starting lists, inline so this migration never changes (app/core/dictionaries.py has the
# same lists for workspaces created later).
DEFAULT_PURPOSES = ("Туризм", "Гостевая", "Бизнес", "Учёба")
DEFAULT_CHANNELS = ("Instagram", "Сарафан", "Партнёр", "Друзья", "Реклама")


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
        "lead_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_lead_channels_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead_channels")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_lead_channels_tenant_name"),
    )
    op.create_index(
        op.f("ix_lead_channels_tenant_id"), "lead_channels", ["tenant_id"], unique=False
    )
    op.create_table(
        "partners",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_partners_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partners")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_partners_id_tenant"),
    )
    op.create_index(op.f("ix_partners_tenant_id"), "partners", ["tenant_id"], unique=False)
    op.create_table(
        "registrars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.Enum("company", "individual", name="registrar_kind"), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_registrars_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registrars")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_registrars_id_tenant"),
    )
    op.create_index(op.f("ix_registrars_tenant_id"), "registrars", ["tenant_id"], unique=False)
    op.create_table(
        "visa_purposes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_visa_purposes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visa_purposes")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_visa_purposes_id_tenant"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_visa_purposes_tenant_name"),
    )
    op.create_index(
        op.f("ix_visa_purposes_tenant_id"), "visa_purposes", ["tenant_id"], unique=False
    )
    op.create_table(
        "partner_terms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("country_id", sa.Uuid(), nullable=True),
        sa.Column(
            "terms_type",
            sa.Enum("percent", "fixed", "none", "unspecified", name="partner_terms_type"),
            nullable=False,
        ),
        sa.Column("percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.CheckConstraint(
            "(terms_type = 'fixed') = (amount IS NOT NULL AND currency IS NOT NULL)",
            name=op.f("ck_partner_terms_amount_only_for_fixed"),
        ),
        sa.CheckConstraint(
            "(terms_type = 'percent') = (percent IS NOT NULL)",
            name=op.f("ck_partner_terms_percent_only_for_percent"),
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount > 0", name=op.f("ck_partner_terms_amount_positive")
        ),
        sa.CheckConstraint(
            "percent IS NULL OR (percent > 0 AND percent <= 100)",
            name=op.f("ck_partner_terms_percent_range"),
        ),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f("fk_partner_terms_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id", "tenant_id"],
            ["partners.id", "partners.tenant_id"],
            name="fk_partner_terms_partner_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_partner_terms_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partner_terms")),
        sa.UniqueConstraint(
            "partner_id",
            "country_id",
            name="uq_partner_terms_partner_country",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_partner_terms_partner_id"), "partner_terms", ["partner_id"], unique=False
    )
    op.create_index(
        op.f("ix_partner_terms_tenant_id"), "partner_terms", ["tenant_id"], unique=False
    )
    op.create_table(
        "prepayments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("country_id", sa.Uuid(), nullable=False),
        sa.Column("purpose_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_prepayments_amount_positive")),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f("fk_prepayments_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purpose_id", "tenant_id"],
            ["visa_purposes.id", "visa_purposes.tenant_id"],
            name="fk_prepayments_purpose_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_prepayments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prepayments")),
        sa.UniqueConstraint(
            "tenant_id",
            "country_id",
            "purpose_id",
            name="uq_prepayments_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_prepayments_tenant_id"), "prepayments", ["tenant_id"], unique=False)
    op.create_table(
        "registrar_prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("registrar_id", sa.Uuid(), nullable=False),
        sa.Column("country_id", sa.Uuid(), nullable=False),
        sa.Column("purpose_id", sa.Uuid(), nullable=True),
        sa.Column("hall", sa.Enum("standard", "premium", name="visa_center_hall"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_registrar_prices_amount_positive")),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f("fk_registrar_prices_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purpose_id", "tenant_id"],
            ["visa_purposes.id", "visa_purposes.tenant_id"],
            name="fk_registrar_prices_purpose_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["registrar_id", "tenant_id"],
            ["registrars.id", "registrars.tenant_id"],
            name="fk_registrar_prices_registrar_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_registrar_prices_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registrar_prices")),
        sa.UniqueConstraint(
            "registrar_id",
            "country_id",
            "purpose_id",
            "hall",
            name="uq_registrar_prices_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_registrar_prices_registrar_id"), "registrar_prices", ["registrar_id"], unique=False
    )
    op.create_index(
        op.f("ix_registrar_prices_tenant_id"), "registrar_prices", ["tenant_id"], unique=False
    )
    op.add_column(
        "tenants",
        sa.Column(
            "payment_currencies",
            sa.ARRAY(sa.String(length=3)),
            server_default=sa.text("'{BYN,PLN,USD,EUR}'"),
            nullable=False,
        ),
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)

    # Existing workspaces get the same starting lists as new ones.
    conn = op.get_bind()
    for table, names in (("visa_purposes", DEFAULT_PURPOSES), ("lead_channels", DEFAULT_CHANNELS)):
        for order, name in enumerate(names):
            conn.execute(
                sa.text(
                    f"INSERT INTO {table} (id, tenant_id, name, sort_order) "
                    "SELECT gen_random_uuid(), id, :name, :order FROM tenants"
                ),
                {"name": name, "order": order},
            )


def downgrade() -> None:
    op.drop_column("tenants", "payment_currencies")
    op.drop_index(op.f("ix_registrar_prices_tenant_id"), table_name="registrar_prices")
    op.drop_index(op.f("ix_registrar_prices_registrar_id"), table_name="registrar_prices")
    op.drop_table("registrar_prices")
    op.drop_index(op.f("ix_prepayments_tenant_id"), table_name="prepayments")
    op.drop_table("prepayments")
    op.drop_index(op.f("ix_partner_terms_tenant_id"), table_name="partner_terms")
    op.drop_index(op.f("ix_partner_terms_partner_id"), table_name="partner_terms")
    op.drop_table("partner_terms")
    op.drop_index(op.f("ix_visa_purposes_tenant_id"), table_name="visa_purposes")
    op.drop_table("visa_purposes")
    op.drop_index(op.f("ix_registrars_tenant_id"), table_name="registrars")
    op.drop_table("registrars")
    op.drop_index(op.f("ix_partners_tenant_id"), table_name="partners")
    op.drop_table("partners")
    op.drop_index(op.f("ix_lead_channels_tenant_id"), table_name="lead_channels")
    op.drop_table("lead_channels")
    for name in ENUMS:
        sa.Enum(name=name).drop(op.get_bind())

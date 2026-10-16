"""consultations, the questionnaire on contacts, passport rules on countries

Passport rules are seeded for Schengen countries from the Schengen Borders Code
(Regulation (EU) 2016/399, art. 6): a travel document issued within the previous 10 years
and valid for at least 3 months after the intended departure. Other countries get no rule
until the admin sets one.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Schengen members in the countries reference (Bulgaria and Romania fully since 2025).
SCHENGEN = (
    "AT", "BE", "BG", "HR", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)  # fmt: skip


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
        "consultations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("held_on", sa.Date(), nullable=False),
        sa.Column("applicants", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("yes", "no", "thinking", name="consultation_decision"),
            nullable=True,
        ),
        sa.Column("lead_channel_id", sa.Uuid(), nullable=True),
        sa.Column(
            "destination_ids", sa.ARRAY(sa.Uuid()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("trip_planned_on", sa.Date(), nullable=True),
        sa.Column("fee_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("fee_currency", sa.String(length=3), nullable=True),
        sa.Column("fee_paid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fee_paid_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "(fee_amount IS NULL) = (fee_currency IS NULL)",
            name=op.f("ck_consultations_fee_amount_with_currency"),
        ),
        sa.CheckConstraint(
            "applicants BETWEEN 1 AND 30", name=op.f("ck_consultations_applicants_range")
        ),
        sa.CheckConstraint(
            "fee_amount IS NULL OR fee_amount > 0", name=op.f("ck_consultations_fee_positive")
        ),
        sa.CheckConstraint(
            "fee_paid_on IS NULL OR fee_paid", name=op.f("ck_consultations_paid_on_only_when_paid")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_consultations_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_consultations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_channel_id", "tenant_id"],
            ["lead_channels.id", "lead_channels.tenant_id"],
            name="fk_consultations_lead_channel_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_consultations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consultations")),
    )
    op.create_index(
        "ix_consultations_contact_held", "consultations", ["contact_id", "held_on"], unique=False
    )
    op.create_index(
        op.f("ix_consultations_tenant_id"), "consultations", ["tenant_id"], unique=False
    )
    op.add_column(
        "contacts",
        sa.Column(
            "questionnaire",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "countries", sa.Column("passport_max_age_years", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "countries", sa.Column("passport_valid_after_months", sa.SmallInteger(), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_countries_passport_max_age_range"),
        "countries",
        "passport_max_age_years IS NULL OR passport_max_age_years BETWEEN 1 AND 30",
    )
    op.create_check_constraint(
        op.f("ck_countries_passport_valid_after_range"),
        "countries",
        "passport_valid_after_months IS NULL OR passport_valid_after_months BETWEEN 0 AND 24",
    )
    enable_tenant_rls("consultations")
    op.get_bind().execute(
        sa.text(
            "UPDATE countries SET passport_max_age_years = 10, passport_valid_after_months = 3 "
            "WHERE code = ANY(:codes)"
        ),
        {"codes": list(SCHENGEN)},
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_countries_passport_valid_after_range"), "countries", type_="check")
    op.drop_constraint(op.f("ck_countries_passport_max_age_range"), "countries", type_="check")
    op.drop_column("countries", "passport_valid_after_months")
    op.drop_column("countries", "passport_max_age_years")
    op.drop_column("contacts", "questionnaire")
    op.drop_index(op.f("ix_consultations_tenant_id"), table_name="consultations")
    op.drop_index("ix_consultations_contact_held", table_name="consultations")
    op.drop_table("consultations")
    sa.Enum(name="consultation_decision").drop(op.get_bind())

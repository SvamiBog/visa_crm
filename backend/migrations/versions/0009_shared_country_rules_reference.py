"""shared reference: countries, visa center operators, appointment rules, visa centers

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("countries", "vac_operators", "country_rules", "visa_centers")
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


def upgrade() -> None:
    op.create_table(
        "countries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column("name_ru", sa.String(length=100), nullable=False),
        sa.Column("name_en", sa.String(length=100), nullable=False),
        sa.Column("consultation_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name=op.f("ck_countries_code_iso_alpha2")),
        sa.CheckConstraint(
            "consultation_minutes IS NULL OR consultation_minutes BETWEEN 5 AND 600",
            name=op.f("ck_countries_consultation_minutes_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_countries")),
        sa.UniqueConstraint("code", name=op.f("uq_countries_code")),
    )
    op.create_table(
        "vac_operators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vac_operators")),
        sa.UniqueConstraint("code", name=op.f("uq_vac_operators_code")),
    )
    op.create_table(
        "country_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column(
            "booking_type",
            sa.Enum(
                "waiting_list",
                "windowed_application",
                "direct_slot",
                "embassy",
                name="booking_type",
            ),
            nullable=False,
        ),
        sa.Column("window_days", sa.SmallInteger(), nullable=True),
        sa.Column("vac_operator_id", sa.Uuid(), nullable=True),
        sa.Column("represented_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "identifier_type",
            sa.Enum(
                "vac_id",
                "application_number",
                "form_number",
                "username",
                name="vac_identifier_type",
            ),
            nullable=True,
        ),
        sa.Column("appeals_accepted", sa.Boolean(), nullable=True),
        sa.Column("appeal_deadline_days", sa.SmallInteger(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(booking_type = 'windowed_application') = (window_days IS NOT NULL)",
            name=op.f("ck_country_rules_window_days_only_for_windowed"),
        ),
        sa.CheckConstraint(
            "appeal_deadline_days IS NULL "
            "OR (appeals_accepted AND appeal_deadline_days BETWEEN 1 AND 365)",
            name=op.f("ck_country_rules_appeal_deadline_needs_appeals"),
        ),
        sa.CheckConstraint(
            "destination_id <> submission_id", name=op.f("ck_country_rules_different_countries")
        ),
        sa.CheckConstraint(
            "window_days IS NULL OR window_days BETWEEN 1 AND 365",
            name=op.f("ck_country_rules_window_days_range"),
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["countries.id"],
            name=op.f("fk_country_rules_destination_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["represented_by_id"],
            ["countries.id"],
            name=op.f("fk_country_rules_represented_by_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["countries.id"],
            name=op.f("fk_country_rules_submission_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vac_operator_id"],
            ["vac_operators.id"],
            name=op.f("fk_country_rules_vac_operator_id_vac_operators"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_country_rules")),
        sa.UniqueConstraint("destination_id", "submission_id", name="uq_country_rules_pair"),
    )
    op.create_index(
        op.f("ix_country_rules_destination_id"), "country_rules", ["destination_id"], unique=False
    )
    op.create_index(
        op.f("ix_country_rules_submission_id"), "country_rules", ["submission_id"], unique=False
    )
    op.create_index(
        op.f("ix_country_rules_vac_operator_id"), "country_rules", ["vac_operator_id"], unique=False
    )
    op.create_table(
        "visa_centers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("city_ru", sa.String(length=100), nullable=False),
        sa.Column("city_en", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["country_rules.id"],
            name=op.f("fk_visa_centers_rule_id_country_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visa_centers")),
        sa.UniqueConstraint("rule_id", "city_ru", name="uq_visa_centers_rule_city"),
    )
    op.create_index(op.f("ix_visa_centers_rule_id"), "visa_centers", ["rule_id"], unique=False)

    for table in TABLES:
        enable_shared_rls(table)


def downgrade() -> None:
    op.drop_index(op.f("ix_visa_centers_rule_id"), table_name="visa_centers")
    op.drop_table("visa_centers")
    op.drop_index(op.f("ix_country_rules_vac_operator_id"), table_name="country_rules")
    op.drop_index(op.f("ix_country_rules_submission_id"), table_name="country_rules")
    op.drop_index(op.f("ix_country_rules_destination_id"), table_name="country_rules")
    op.drop_table("country_rules")
    op.drop_table("vac_operators")
    op.drop_table("countries")
    sa.Enum(name="vac_identifier_type").drop(op.get_bind())
    sa.Enum(name="booking_type").drop(op.get_bind())

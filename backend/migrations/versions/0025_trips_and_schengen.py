"""trips of a client for the 90/180 calculator; the day each country joined Schengen

Iceland, Liechtenstein, Norway and Switzerland are added to the countries reference:
days there count towards 90/180 too. Dates are when each country started applying the
Schengen rules (Bulgaria and Romania: 31 March 2024, Croatia: 1 January 2023). Cyprus
and Ireland are not in the area.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-17

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHENGEN_SINCE = {
    "AT": date(1997, 12, 1),
    "BE": date(1995, 3, 26),
    "BG": date(2024, 3, 31),
    "CH": date(2008, 12, 12),
    "CZ": date(2007, 12, 21),
    "DE": date(1995, 3, 26),
    "DK": date(2001, 3, 25),
    "EE": date(2007, 12, 21),
    "ES": date(1995, 3, 26),
    "FI": date(2001, 3, 25),
    "FR": date(1995, 3, 26),
    "GR": date(2000, 3, 26),
    "HR": date(2023, 1, 1),
    "HU": date(2007, 12, 21),
    "IS": date(2001, 3, 25),
    "IT": date(1997, 10, 26),
    "LI": date(2011, 12, 19),
    "LT": date(2007, 12, 21),
    "LU": date(1995, 3, 26),
    "LV": date(2007, 12, 21),
    "MT": date(2007, 12, 21),
    "NL": date(1995, 3, 26),
    "NO": date(2001, 3, 25),
    "PL": date(2007, 12, 21),
    "PT": date(1995, 3, 26),
    "RO": date(2024, 3, 31),
    "SE": date(2001, 3, 25),
    "SI": date(2007, 12, 21),
    "SK": date(2007, 12, 21),
}
# Schengen members missing from the reference seeded in 0010.
NEW_COUNTRIES = [
    ("IS", "Исландия", "Iceland"),
    ("LI", "Лихтенштейн", "Liechtenstein"),
    ("NO", "Норвегия", "Norway"),
    ("CH", "Швейцария", "Switzerland"),
]


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    op.add_column("countries", sa.Column("schengen_since", sa.Date(), nullable=True))
    # The migration runs as the database owner, outside the app role: RLS does not apply.
    conn = op.get_bind()
    for code, name_ru, name_en in NEW_COUNTRIES:
        # Same rules as the other Schengen destinations (migrations 0010 and 0013).
        conn.execute(
            sa.text(
                "INSERT INTO countries (id, code, name_ru, name_en, consultation_minutes, "
                "passport_max_age_years, passport_valid_after_months) "
                "VALUES (gen_random_uuid(), :code, :ru, :en, 30, 10, 3) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "ru": name_ru, "en": name_en},
        )
    for code, since in SCHENGEN_SINCE.items():
        conn.execute(
            sa.text("UPDATE countries SET schengen_since = :since WHERE code = :code"),
            {"code": code, "since": since},
        )

    op.create_table(
        "trips",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("country_id", sa.Uuid(), nullable=False),
        sa.Column("entered_on", sa.Date(), nullable=False),
        sa.Column("left_on", sa.Date(), nullable=True),
        sa.Column("long_stay", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
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
            "left_on IS NULL OR left_on >= entered_on", name=op.f("ck_trips_dates_order")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_trips_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f("fk_trips_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_trips_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_trips_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trips")),
    )
    op.create_index("ix_trips_contact_entered", "trips", ["contact_id", "entered_on"])
    op.create_index(op.f("ix_trips_country_id"), "trips", ["country_id"])
    op.create_index(op.f("ix_trips_tenant_id"), "trips", ["tenant_id"])
    enable_tenant_rls("trips")


def downgrade() -> None:
    op.drop_table("trips")
    # The added countries stay: cases and rules may refer to them by now.
    op.drop_column("countries", "schengen_since")

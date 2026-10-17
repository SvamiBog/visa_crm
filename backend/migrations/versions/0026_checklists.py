"""document checklists: shared items (admin) and a specialist's own; purpose kinds

A specialist's visa purposes get a kind shared by the platform (the purposes of the
Schengen form, plus work): the shared checklists go by it. The starting purposes of
every workspace (app/core/dictionaries.py) get theirs by name.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSE_KINDS = (
    "tourism",
    "visit",
    "business",
    "study",
    "work",
    "culture",
    "sports",
    "official",
    "medical",
    "transit",
    "other",
)
purpose_kind = postgresql.ENUM(*PURPOSE_KINDS, name="purpose_kind", create_type=False)
# The starting purposes, as a new workspace gets them.
DEFAULT_KINDS = {"Туризм": "tourism", "Гостевая": "visit", "Бизнес": "business", "Учёба": "study"}

SYSTEM = "current_setting('app.system', true) = 'on'"
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
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def item_columns(table: str) -> list[sa.Column | sa.Constraint]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("country_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column(
            "purposes",
            postgresql.ARRAY(purpose_kind),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_age_days", sa.SmallInteger(), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "max_age_days IS NULL OR max_age_days BETWEEN 1 AND 365",
            name=op.f(f"ck_{table}_max_age_days_range"),
        ),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name=op.f(f"fk_{table}_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["countries.id"],
            name=op.f(f"fk_{table}_submission_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    purpose_kind.create(op.get_bind())
    op.add_column("visa_purposes", sa.Column("kind", purpose_kind, nullable=True))
    # The migration runs as the database owner, outside the app role: RLS does not apply.
    for name, kind in DEFAULT_KINDS.items():
        op.execute(
            sa.text(
                "UPDATE visa_purposes SET kind = CAST(:kind AS purpose_kind) "
                "WHERE name = :name AND kind IS NULL"
            ).bindparams(name=name, kind=kind)
        )

    op.create_table("checklist_items", *item_columns("checklist_items"))
    op.create_index("ix_checklist_items_country_id", "checklist_items", ["country_id"])
    enable_shared_rls("checklist_items")

    op.create_table(
        "own_checklist_items",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *item_columns("own_checklist_items"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_own_checklist_items_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_own_checklist_items_id_tenant"),
    )
    op.create_index(
        "ix_own_checklist_items_tenant_country", "own_checklist_items", ["tenant_id", "country_id"]
    )
    enable_tenant_rls("own_checklist_items")


def downgrade() -> None:
    op.drop_table("own_checklist_items")
    op.drop_table("checklist_items")
    op.drop_column("visa_purposes", "kind")
    purpose_kind.drop(op.get_bind())

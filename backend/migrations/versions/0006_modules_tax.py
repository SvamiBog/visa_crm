"""modules per specialist and employee, tax rate, user names

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stage-1 core modules at the time of this migration (inline: migrations never change).
CORE_MODULES = ["clients", "cases", "tasks", "country_rules", "analytics"]


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "tax_rate",
            sa.Numeric(precision=5, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "modules",
            postgresql.ARRAY(sa.String(length=40)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("full_name", sa.String(length=200), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "modules",
            postgresql.ARRAY(sa.String(length=40)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    # Existing specialists keep working: they get the core modules.
    op.execute(
        sa.text("UPDATE tenants SET modules = :modules").bindparams(
            sa.bindparam("modules", CORE_MODULES, type_=postgresql.ARRAY(sa.String()))
        )
    )


def downgrade() -> None:
    op.drop_column("users", "modules")
    op.drop_column("users", "full_name")
    op.drop_column("tenants", "modules")
    op.drop_column("tenants", "tax_rate")

"""interface theme per user

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ui_theme", sa.String(length=16), server_default="blue", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "ui_theme")

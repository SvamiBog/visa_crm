"""a deleted case's number is never given again

The specialist may delete a case opened by mistake, a duplicate (pilot fix 1,
docs/decisions.md → «Пилот: правки визовика (2026-11-19)»). Case numbers were taken as
the highest existing number plus one, so deleting the newest case would give its number
to the next one. The workspace keeps the highest number of a deleted case, and numbering
goes on past it. A plain column on tenants: the row is the workspace's own (RLS by id).

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "max_deleted_case_number", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )


def downgrade() -> None:
    """Drops the column: a deleted newest case's number may be given again."""
    op.drop_column("tenants", "max_deleted_case_number")

"""a workspace's tax rate may be not set yet; 0 means no tax

The specialist now sets her tax rate herself in «Настройки» (pilot T8, owner's answers of
2026-11-20, docs/decisions.md → «Пилот: правки визовика»). An empty rate is «не задана»:
the case finances ask for it instead of counting a tax of 0%; a rate of 0 is a real
choice, «без налога». Until now 0 was the default of a new workspace and stood for «not
set» (pilot fix 5), so existing zeros were never chosen: they become NULL.

Downgrade: a rate not set reads 0 again, the old default; an explicit 0 and «not set» are
one value there, so which one it was is lost.

tenants already has RLS; the column change touches no policy.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "tax_rate",
        existing_type=sa.Numeric(5, 2),
        nullable=True,
        server_default=None,
    )
    # The migration runs as the tables' owner, which RLS does not restrict.
    op.execute("UPDATE tenants SET tax_rate = NULL WHERE tax_rate = 0")


def downgrade() -> None:
    op.execute("UPDATE tenants SET tax_rate = 0 WHERE tax_rate IS NULL")
    op.alter_column(
        "tenants",
        "tax_rate",
        existing_type=sa.Numeric(5, 2),
        nullable=False,
        server_default=sa.text("0"),
    )

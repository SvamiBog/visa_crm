"""the currency of a case's price

The specialist names the price of a case in BYN, PLN or sometimes USD (pilot fix 4,
2026-11-19, docs/decisions.md → «Пилот: правки визовика»). The price, the prepayment and
the consultation credit are kept in the case's own currency, with the rate of that
currency to the specialist's base one (base units for one unit, like payments). Existing
cases were priced in the base currency: they get it, at the rate 1.

case_finances already has RLS and the employee_scope policy; the new columns change
neither.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("case_finances", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column(
        "case_finances",
        sa.Column("rate", sa.Numeric(14, 6), server_default=sa.text("1"), nullable=False),
    )
    # The migration runs as the tables' owner, which RLS does not restrict.
    op.execute(
        "UPDATE case_finances f SET currency = t.base_currency FROM tenants t "
        "WHERE t.id = f.tenant_id"
    )
    op.alter_column("case_finances", "currency", nullable=False)
    op.create_check_constraint(op.f("ck_case_finances_rate_positive"), "case_finances", "rate > 0")


def downgrade() -> None:
    """Drops the currency: a price named in another currency would read as the base one,
    so cases priced in another currency are converted to the base one first."""
    op.execute(
        "UPDATE case_finances f SET price = round(f.price * f.rate, 2), "
        "prepayment = round(f.prepayment * f.rate, 2), "
        "consultation_credit = round(f.consultation_credit * f.rate, 2) "
        "FROM tenants t WHERE t.id = f.tenant_id AND f.currency <> t.base_currency"
    )
    op.drop_constraint(op.f("ck_case_finances_rate_positive"), "case_finances", type_="check")
    op.drop_column("case_finances", "rate")
    op.drop_column("case_finances", "currency")

"""a prepayment in the list keeps the currency it was entered in

The specialist may now change her base currency (pilot T8). The owner's answer of
2026-11-20: a prepayment of the list stays as she entered it (150 BYN stays 150 BYN), it is
not converted with the base currency; a case priced in another currency gets it converted
at the bank's rate. So each prepayment keeps its currency; the existing ones were in the
workspace's base currency, which they get.

Downgrade: the column goes; a prepayment entered in another currency than the base one
would then read as the base currency, so it is converted to it first at the latest rate
the site keeps (through BYN); without such a rate it keeps its number.

prepayments already has RLS; the new column changes no policy.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("prepayments", sa.Column("currency", sa.String(3), nullable=True))
    # The migration runs as the tables' owner, which RLS does not restrict.
    op.execute(
        "UPDATE prepayments p SET currency = t.base_currency FROM tenants t "
        "WHERE t.id = p.tenant_id"
    )
    op.alter_column("prepayments", "currency", nullable=False)


def downgrade() -> None:
    # BYN for one unit of a currency: the latest rate the site keeps; BYN itself is 1.
    op.execute(
        """
        WITH byn AS (
            (
                SELECT DISTINCT ON (currency) currency, rate FROM exchange_rates
                ORDER BY currency, on_date DESC
            )
            UNION ALL SELECT 'BYN', 1
        )
        UPDATE prepayments p
        SET amount = greatest(round(p.amount * f.rate / b.rate, 2), 0.01)
        FROM tenants t, byn f, byn b
        WHERE t.id = p.tenant_id AND p.currency <> t.base_currency
          AND f.currency = p.currency AND b.currency = t.base_currency
        """
    )
    op.drop_column("prepayments", "currency")

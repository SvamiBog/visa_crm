"""settled talons: a talon paid to the registrar outside the CRM's settlements

A talon imported from the specialist's tables was paid before the CRM (decisions.md →
«Импорт таблиц», 2026-10-18): it counts in the case's profit, not in the registrar's
balance. The specialist can untick it in the case finances.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_finances",
        sa.Column(
            "registrar_fee_settled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_case_finances_settled_with_talon"),
        "case_finances",
        "NOT registrar_fee_settled OR registrar_fee_amount IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_case_finances_settled_with_talon"), "case_finances", type_="check")
    op.drop_column("case_finances", "registrar_fee_settled")

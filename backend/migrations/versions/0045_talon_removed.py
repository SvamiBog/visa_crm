"""what the specialist took off a case's finances is not filled in again from the lists

Pilot (backlog 1.15.12): a case with a talon is not deleted, and the refusal says to take
the talon off in the case finances. The form filled an empty talon from the registrar's
price list, so the next save of the finances charged it again and the case could not be
deleted. The list's prepayment, the consultation credit and the partner's terms came back
the same way (owner's answer 2026-11-24). A case now remembers each one the specialist
took off; the lists suggest it only while it was never taken off, or after she typed one
again. Existing rows: nothing taken off.

Downgrade: the columns go; what was taken off is suggested again from the lists.

case_finances already has RLS; the new columns change no policy.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    "registrar_fee_removed",
    "prepayment_removed",
    "consultation_credit_removed",
    "partner_terms_removed",
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            "case_finances",
            sa.Column(column, sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("case_finances", column)

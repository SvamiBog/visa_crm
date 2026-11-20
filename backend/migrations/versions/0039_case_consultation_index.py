"""an index on the consultation a case was opened from

The client index now asks for every contact which cases came from their consultations:
a case opened from the daughter's consultation for her father takes her to «В работе»,
and her consultation lists it (pilot fixes 2 and 8, docs/decisions.md → «Пилот: правки
визовика (2026-11-19)», T2). The lookup goes by visa_cases.consultation_id, which had no
index of its own.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-20

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_visa_cases_consultation_id", "visa_cases", ["consultation_id"])


def downgrade() -> None:
    op.drop_index("ix_visa_cases_consultation_id", table_name="visa_cases")

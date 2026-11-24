"""the day the client is to bring the documents: «Документы до» in the case

The specialist's answer to question 26 (2026-11-24, docs/decisions.md → «Ответы визовика
(2026-11-24)»): she sets the reminder dates herself. The questionnaire already has its
day in the case; the documents get theirs, and the reminder of an incomplete package
counts from it when it is set (app/core/reminders.py → package). A plain nullable date on
a table that already has RLS and the employee_scope policy: nothing new to secure.

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("visa_cases", sa.Column("documents_due_on", sa.Date(), nullable=True))


def downgrade() -> None:
    """Drops the date: the days set for the documents are lost."""
    op.drop_column("visa_cases", "documents_due_on")

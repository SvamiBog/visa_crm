"""dates for reminders: the follow-up after a consultation and the form questionnaire

Backlog 1.19 (docs/decisions.md → «Напоминания клиентам»): the specialist reminds
clients of what she now keeps in her head. A consultation gets the day agreed to hear
the client's decision; a case gets when the questionnaire for the application form is
due back and when it came. Plain nullable dates on tables that already have RLS and the
employee_scope policy: nothing new to secure.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("consultations", sa.Column("follow_up_on", sa.Date(), nullable=True))
    op.add_column("visa_cases", sa.Column("questionnaire_due_on", sa.Date(), nullable=True))
    op.add_column("visa_cases", sa.Column("questionnaire_received_on", sa.Date(), nullable=True))


def downgrade() -> None:
    """Drops the three dates: the follow-up days and questionnaire dates are lost."""
    op.drop_column("visa_cases", "questionnaire_received_on")
    op.drop_column("visa_cases", "questionnaire_due_on")
    op.drop_column("consultations", "follow_up_on")

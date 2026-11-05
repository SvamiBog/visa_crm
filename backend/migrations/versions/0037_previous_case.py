"""a case filed again: the link to the case it repeats

After a refusal, or a client who did not submit, the specialist files again — a new case
linked to the previous one (docs/clients-module.md → «Этапы кейса»; specialist run before
the pilot, docs/decisions.md → «Повторная подача», 2026-11-04). A nullable column on
visa_cases, which already has RLS and the employee_scope policy; the link is a composite
key within the workspace, so it cannot point to another specialist's case, and it is
cleared when the previous case goes.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("visa_cases", sa.Column("previous_case_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_visa_cases_previous_case_id_same_tenant",
        "visa_cases",
        "visa_cases",
        ["previous_case_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="SET NULL (previous_case_id)",
    )
    op.create_index("ix_visa_cases_previous_case_id", "visa_cases", ["previous_case_id"])


def downgrade() -> None:
    """Drops the link: which case a repeat filing followed is lost."""
    op.drop_index("ix_visa_cases_previous_case_id", table_name="visa_cases")
    op.drop_constraint(
        "fk_visa_cases_previous_case_id_same_tenant", "visa_cases", type_="foreignkey"
    )
    op.drop_column("visa_cases", "previous_case_id")

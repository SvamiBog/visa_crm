"""daily limits of an employee: client cards and cases opened, vault passwords revealed

Backlog Т.14 (docs/decisions.md → «Защита данных от копирования сотрудником»). An
employee's opens go to the audit log; the index counts today's quickly. In an employee's
session the audit log shows and takes only their own events (policy employee_scope, as
in 0031): the count needs no one else's.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWN = "app_employee_id() IS NULL OR actor_id = app_employee_id()"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("daily_card_limit", sa.SmallInteger(), server_default="50", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("daily_password_limit", sa.SmallInteger(), server_default="20", nullable=False),
    )
    op.create_check_constraint(
        "daily_card_limit_range", "users", "daily_card_limit BETWEEN 1 AND 1000"
    )
    op.create_check_constraint(
        "daily_password_limit_range", "users", "daily_password_limit BETWEEN 1 AND 1000"
    )
    op.create_index(
        "ix_audit_events_actor_action_time",
        "audit_events",
        ["actor_id", "action", "occurred_at"],
    )
    op.execute(
        f"CREATE POLICY employee_scope ON audit_events AS RESTRICTIVE "
        f"USING ({OWN}) WITH CHECK ({OWN})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY employee_scope ON audit_events")
    op.drop_index("ix_audit_events_actor_action_time", table_name="audit_events")
    op.drop_constraint("daily_password_limit_range", "users", type_="check")
    op.drop_constraint("daily_card_limit_range", "users", type_="check")
    op.drop_column("users", "daily_password_limit")
    op.drop_column("users", "daily_card_limit")

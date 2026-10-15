"""workspace scope may manage sessions and recovery codes of its own users

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM = "current_setting('app.system', true) = 'on'"
# The users subquery is itself under RLS, so it only ever returns the workspace's users.
OWN_USERS = (
    f"{SYSTEM} OR user_id IN (SELECT id FROM users "
    "WHERE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
)
TABLES = ("user_sessions", "user_recovery_codes")


def upgrade() -> None:
    # A specialist signs out an employee they block or give a new password.
    for table in TABLES:
        op.execute(f"DROP POLICY system_only ON {table}")
        op.execute(
            f"CREATE POLICY own_users ON {table} USING ({OWN_USERS}) WITH CHECK ({OWN_USERS})"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY own_users ON {table}")
        op.execute(f"CREATE POLICY system_only ON {table} USING ({SYSTEM}) WITH CHECK ({SYSTEM})")

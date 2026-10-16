"""a new login email waiting for its confirmation code

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_changes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("new_email", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "new_email = lower(new_email)", name=op.f("ck_email_changes_new_email_lowercase")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_changes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_email_changes")),
    )
    # Like sessions and recovery codes: only the login flow (system scope) sees them.
    rule = "current_setting('app.system', true) = 'on'"
    op.execute("ALTER TABLE email_changes ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY system_only ON email_changes USING ({rule}) WITH CHECK ({rule})")


def downgrade() -> None:
    op.drop_table("email_changes")

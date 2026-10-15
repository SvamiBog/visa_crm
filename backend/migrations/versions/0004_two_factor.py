"""two-factor login: TOTP and recovery codes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column(
        "user_sessions",
        sa.Column("mfa_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_recovery_codes")),
    )
    op.create_index(
        op.f("ix_user_recovery_codes_user_id"), "user_recovery_codes", ["user_id"], unique=False
    )
    # Like sessions: only the login flow (system scope) reads or writes recovery codes.
    rule = "current_setting('app.system', true) = 'on'"
    op.execute("ALTER TABLE user_recovery_codes ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY system_only ON user_recovery_codes USING ({rule}) WITH CHECK ({rule})"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_recovery_codes_user_id"), table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_column("user_sessions", "mfa_pending")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret_encrypted")

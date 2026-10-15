"""append-only audit log

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "visacrm_app"


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_tenant_id_id", "audit_events", ["tenant_id", "id"])

    # Each workspace reads and writes only its own events; the system scope sees all.
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON audit_events USING ({rule}) WITH CHECK ({rule})")

    # Append-only: the app role may only read and add rows...
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {APP_ROLE}")
    # ...and nobody may change history, not even the table owner.
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")

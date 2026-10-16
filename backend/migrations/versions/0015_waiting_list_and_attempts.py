"""waiting list entries and application attempts; the visa center number and the
application email on cases

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL_OWNER = postgresql.ENUM(
    "client", "specialist", "registrar", name="email_owner", create_type=False
)


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def case_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
    ]


def common_constraints(table: str) -> list:
    return [
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name=f"fk_{table}_case_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f(f"fk_{table}_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f(f"fk_{table}_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # Queue entries and attempts refer to their case within the same workspace.
    op.create_unique_constraint("uq_visa_cases_id_tenant", "visa_cases", ["id", "tenant_id"])
    EMAIL_OWNER.create(op.get_bind())
    op.add_column("visa_cases", sa.Column("vac_number", sa.String(length=64), nullable=True))
    op.add_column(
        "visa_cases", sa.Column("application_email", sa.String(length=320), nullable=True)
    )
    op.add_column("visa_cases", sa.Column("application_email_owner", EMAIL_OWNER, nullable=True))

    op.create_table(
        "waiting_list_entries",
        *case_columns(),
        sa.Column("queued_on", sa.Date(), nullable=False),
        sa.Column("letter_on", sa.Date(), nullable=True),
        sa.Column("removed_on", sa.Date(), nullable=True),
        sa.Column("removed_reason", sa.String(length=500), nullable=True),
        *audit_columns(),
        sa.CheckConstraint(
            "letter_on IS NULL OR letter_on >= queued_on",
            name=op.f("ck_waiting_list_entries_letter_after_queued"),
        ),
        sa.CheckConstraint(
            "removed_on IS NULL OR removed_on >= queued_on",
            name=op.f("ck_waiting_list_entries_removed_after_queued"),
        ),
        sa.CheckConstraint(
            "removed_reason IS NULL OR removed_on IS NOT NULL",
            name=op.f("ck_waiting_list_entries_reason_only_when_removed"),
        ),
        *common_constraints("waiting_list_entries"),
    )
    op.create_index(op.f("ix_waiting_list_entries_case_id"), "waiting_list_entries", ["case_id"])
    op.create_index(
        op.f("ix_waiting_list_entries_tenant_id"), "waiting_list_entries", ["tenant_id"]
    )
    op.create_index(
        "uq_waiting_list_entries_open_case",
        "waiting_list_entries",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("removed_on IS NULL"),
    )

    op.create_table(
        "application_attempts",
        *case_columns(),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("deadline_on", sa.Date(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum("date_received", "expired", "cancelled", name="attempt_outcome"),
            nullable=True,
        ),
        sa.Column("closed_on", sa.Date(), nullable=True),
        *audit_columns(),
        sa.CheckConstraint(
            "closed_on IS NULL OR outcome IS NOT NULL",
            name=op.f("ck_application_attempts_closed_with_outcome"),
        ),
        sa.CheckConstraint(
            "deadline_on >= sent_on", name=op.f("ck_application_attempts_deadline_after_sent")
        ),
        sa.CheckConstraint("number > 0", name=op.f("ck_application_attempts_number_positive")),
        *common_constraints("application_attempts"),
        sa.UniqueConstraint("case_id", "number", name="uq_application_attempts_case_number"),
    )
    op.create_index(op.f("ix_application_attempts_case_id"), "application_attempts", ["case_id"])
    op.create_index(
        op.f("ix_application_attempts_tenant_id"), "application_attempts", ["tenant_id"]
    )
    op.create_index(
        "uq_application_attempts_open_case",
        "application_attempts",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("outcome IS NULL"),
    )

    enable_tenant_rls("waiting_list_entries")
    enable_tenant_rls("application_attempts")


def downgrade() -> None:
    op.drop_table("application_attempts")
    op.drop_table("waiting_list_entries")
    op.execute("DROP TYPE attempt_outcome")
    op.drop_column("visa_cases", "application_email_owner")
    op.drop_column("visa_cases", "application_email")
    op.drop_column("visa_cases", "vac_number")
    EMAIL_OWNER.drop(op.get_bind())
    op.drop_constraint("uq_visa_cases_id_tenant", "visa_cases", type_="unique")

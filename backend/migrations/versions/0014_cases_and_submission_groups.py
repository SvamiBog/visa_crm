"""cases (visa_cases) and submission groups

A case is one visa for one applicant; a family applying together is a submission group of
several cases. Both tables carry tenant_id under Row-Level Security; children point to
their parents through (parent_id, tenant_id).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_ENUMS = ("case_stage", "visa_outcome", "visa_entries")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # Cases refer to the consultation they came from within the same workspace.
    op.create_unique_constraint("uq_consultations_id_tenant", "consultations", ["id", "tenant_id"])

    op.create_table(
        "submission_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_submission_groups_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_groups")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_submission_groups_id_tenant"),
    )
    op.create_index(
        op.f("ix_submission_groups_tenant_id"), "submission_groups", ["tenant_id"], unique=False
    )

    op.create_table(
        "visa_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("consultation_id", sa.Uuid(), nullable=True),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("purpose_id", sa.Uuid(), nullable=True),
        sa.Column("submission_country_id", sa.Uuid(), nullable=False),
        sa.Column("visa_center_id", sa.Uuid(), nullable=True),
        sa.Column("registrar_id", sa.Uuid(), nullable=True),
        sa.Column(
            "hall",
            postgresql.ENUM("standard", "premium", name="visa_center_hall", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "stage",
            sa.Enum("waiting", "documents", "submission", "review", "result", name="case_stage"),
            server_default="waiting",
            nullable=False,
        ),
        sa.Column("submission_on", sa.Date(), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum("visa", "refusal", "not_submitted", name="visa_outcome"),
            nullable=True,
        ),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column(
            "visa_entries",
            sa.Enum("single", "double", "multiple", name="visa_entries"),
            nullable=True,
        ),
        sa.Column("visa_valid_from", sa.Date(), nullable=True),
        sa.Column("visa_valid_to", sa.Date(), nullable=True),
        sa.Column("stay_days", sa.SmallInteger(), nullable=True),
        sa.Column("after_appeal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT after_appeal OR outcome IN ('visa', 'refusal')",
            name=op.f("ck_visa_cases_appeal_needs_decision"),
        ),
        sa.CheckConstraint(
            "outcome = 'visa' OR (visa_entries IS NULL AND visa_valid_from IS NULL "
            "AND visa_valid_to IS NULL AND stay_days IS NULL)",
            name=op.f("ck_visa_cases_visa_details_only_for_visa"),
        ),
        sa.CheckConstraint(
            "decided_on IS NULL OR outcome IS NOT NULL",
            name=op.f("ck_visa_cases_decided_on_with_outcome"),
        ),
        sa.CheckConstraint("number > 0", name=op.f("ck_visa_cases_number_positive")),
        sa.CheckConstraint(
            "stay_days IS NULL OR stay_days BETWEEN 1 AND 3660",
            name=op.f("ck_visa_cases_stay_days_range"),
        ),
        sa.CheckConstraint(
            "visa_valid_from IS NULL OR visa_valid_to IS NULL OR visa_valid_to >= visa_valid_from",
            name=op.f("ck_visa_cases_visa_validity_order"),
        ),
        sa.ForeignKeyConstraint(
            ["consultation_id", "tenant_id"],
            ["consultations.id", "consultations.tenant_id"],
            name="fk_visa_cases_consultation_id_same_tenant",
            ondelete="SET NULL (consultation_id)",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_visa_cases_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_visa_cases_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["countries.id"],
            name=op.f("fk_visa_cases_destination_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "tenant_id"],
            ["submission_groups.id", "submission_groups.tenant_id"],
            name="fk_visa_cases_group_id_same_tenant",
            ondelete="SET NULL (group_id)",
        ),
        sa.ForeignKeyConstraint(
            ["purpose_id", "tenant_id"],
            ["visa_purposes.id", "visa_purposes.tenant_id"],
            name="fk_visa_cases_purpose_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["registrar_id", "tenant_id"],
            ["registrars.id", "registrars.tenant_id"],
            name="fk_visa_cases_registrar_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_country_id"],
            ["countries.id"],
            name=op.f("fk_visa_cases_submission_country_id_countries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_visa_cases_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["visa_center_id"],
            ["visa_centers.id"],
            name=op.f("fk_visa_cases_visa_center_id_visa_centers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visa_cases")),
        sa.UniqueConstraint("tenant_id", "number", name="uq_visa_cases_tenant_number"),
    )
    op.create_index("ix_visa_cases_contact_id", "visa_cases", ["contact_id"], unique=False)
    op.create_index(
        op.f("ix_visa_cases_destination_id"), "visa_cases", ["destination_id"], unique=False
    )
    op.create_index("ix_visa_cases_group_id", "visa_cases", ["group_id"], unique=False)
    op.create_index(op.f("ix_visa_cases_tenant_id"), "visa_cases", ["tenant_id"], unique=False)
    op.create_index(
        "ix_visa_cases_tenant_stage", "visa_cases", ["tenant_id", "stage"], unique=False
    )

    enable_tenant_rls("submission_groups")
    enable_tenant_rls("visa_cases")


def downgrade() -> None:
    op.drop_table("visa_cases")
    op.drop_table("submission_groups")
    for name in NEW_ENUMS:
        op.execute(f"DROP TYPE {name}")
    op.drop_constraint("uq_consultations_id_tenant", "consultations", type_="unique")

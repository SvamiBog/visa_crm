"""document templates: a specialist's Word files with codes, and templates given to clients

The filled file is made on every download and never stored; the blank template lives in
the file storage, encrypted (app/models/template.py).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("file_name", sa.String(length=200), nullable=True),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "codes",
            postgresql.ARRAY(sa.String(length=40)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "unknown_codes",
            postgresql.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_templates_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_document_templates_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_templates")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_document_templates_id_tenant"),
    )
    op.create_index(op.f("ix_document_templates_tenant_id"), "document_templates", ["tenant_id"])
    enable_tenant_rls("document_templates")

    op.create_table(
        "contact_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_contact_templates_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id", "tenant_id"],
            ["document_templates.id", "document_templates.tenant_id"],
            name="fk_contact_templates_template_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name="fk_contact_templates_case_id_same_tenant",
            ondelete="SET NULL (case_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contact_templates_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_contact_templates_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_templates")),
    )
    op.create_index("ix_contact_templates_contact_id", "contact_templates", ["contact_id"])
    op.create_index("ix_contact_templates_template_id", "contact_templates", ["template_id"])
    op.create_index(op.f("ix_contact_templates_tenant_id"), "contact_templates", ["tenant_id"])
    enable_tenant_rls("contact_templates")


def downgrade() -> None:
    op.drop_table("contact_templates")
    op.drop_table("document_templates")

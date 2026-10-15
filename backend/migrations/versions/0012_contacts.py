"""contacts: the card, the notes timeline and links between contacts

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("contacts", "contact_notes", "contact_relations")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # contacts refer to a lead channel through (id, tenant_id): that pair must be unique first.
    op.create_unique_constraint("uq_lead_channels_id_tenant", "lead_channels", ["id", "tenant_id"])
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("full_name_latin", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("telegram", sa.String(length=64), nullable=True),
        sa.Column("messenger", sa.Enum("whatsapp", "viber", name="messenger"), nullable=True),
        sa.Column("messenger_contact", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("lead_channel_id", sa.Uuid(), nullable=True),
        sa.Column("partner_id", sa.Uuid(), nullable=True),
        sa.Column("consent_signed_on", sa.Date(), nullable=True),
        sa.Column("contract_number", sa.String(length=50), nullable=True),
        sa.Column("contract_signed_on", sa.Date(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_contacts_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_channel_id", "tenant_id"],
            ["lead_channels.id", "lead_channels.tenant_id"],
            name="fk_contacts_lead_channel_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id", "tenant_id"],
            ["partners.id", "partners.tenant_id"],
            name="fk_contacts_partner_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contacts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_contacts_id_tenant"),
    )
    op.create_index(op.f("ix_contacts_tenant_id"), "contacts", ["tenant_id"], unique=False)
    op.create_index(
        "uq_contacts_tenant_phone",
        "contacts",
        ["tenant_id", "phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )
    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_contact_notes_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_contact_notes_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contact_notes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_notes")),
    )
    op.create_index(
        "ix_contact_notes_contact_created",
        "contact_notes",
        ["contact_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contact_notes_tenant_id"), "contact_notes", ["tenant_id"], unique=False
    )
    op.create_table(
        "contact_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("related_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("contact_id <> related_id", name=op.f("ck_contact_relations_not_self")),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_contact_relations_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_contact_relations_related_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contact_relations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_relations")),
        sa.UniqueConstraint("contact_id", "related_id", name="uq_contact_relations_pair"),
    )
    op.create_index(
        op.f("ix_contact_relations_contact_id"), "contact_relations", ["contact_id"], unique=False
    )
    op.create_index(
        op.f("ix_contact_relations_tenant_id"), "contact_relations", ["tenant_id"], unique=False
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_relations_tenant_id"), table_name="contact_relations")
    op.drop_index(op.f("ix_contact_relations_contact_id"), table_name="contact_relations")
    op.drop_table("contact_relations")
    op.drop_index(op.f("ix_contact_notes_tenant_id"), table_name="contact_notes")
    op.drop_index("ix_contact_notes_contact_created", table_name="contact_notes")
    op.drop_table("contact_notes")
    op.drop_index(
        "uq_contacts_tenant_phone",
        table_name="contacts",
        postgresql_where=sa.text("phone IS NOT NULL"),
    )
    op.drop_index(op.f("ix_contacts_tenant_id"), table_name="contacts")
    op.drop_table("contacts")
    op.drop_constraint("uq_lead_channels_id_tenant", "lead_channels", type_="unique")
    sa.Enum(name="messenger").drop(op.get_bind())

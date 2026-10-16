"""the vault: systems with password rules and accounts with encrypted passwords; the
«vault» module; the specialist's backup mailbox as an email owner

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL_OWNER = postgresql.ENUM(name="email_owner", create_type=False)
SYSTEM_KIND = postgresql.ENUM(
    "email", "visa_center", "embassy", "booking", "other", name="vault_system_kind"
)
# Starting systems, inline so this migration never changes (app/core/dictionaries.py has
# the same list for workspaces created later).
DEFAULT_SYSTEMS = (
    ("Почта", "email"),
    ("VFS Global", "visa_center"),
    ("TLScontact", "visa_center"),
    ("BLS International", "visa_center"),
    ("Сайт посольства", "embassy"),
    ("Booking.com", "booking"),
)


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def upgrade() -> None:
    # A new value is not used in this transaction, so adding it here is allowed.
    op.execute("ALTER TYPE email_owner ADD VALUE 'specialist_backup' AFTER 'specialist'")

    op.create_table(
        "vault_systems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", SYSTEM_KIND, nullable=False),
        sa.Column("url", sa.String(length=300), nullable=True),
        sa.Column(
            "password_length", sa.SmallInteger(), server_default=sa.text("12"), nullable=False
        ),
        sa.Column("password_lower", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("password_upper", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("password_digits", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "password_symbols", sa.String(length=32), server_default="!@#$%*?", nullable=False
        ),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "password_length BETWEEN 6 AND 64",
            name=op.f("ck_vault_systems_password_length_range"),
        ),
        sa.CheckConstraint(
            "password_lower OR password_upper OR password_digits OR password_symbols <> ''",
            name=op.f("ck_vault_systems_password_has_characters"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_vault_systems_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vault_systems")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_vault_systems_id_tenant"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_vault_systems_tenant_name"),
    )
    op.create_index(op.f("ix_vault_systems_tenant_id"), "vault_systems", ["tenant_id"])

    op.create_table(
        "vault_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("login", sa.String(length=320), nullable=False),
        sa.Column("email_owner", EMAIL_OWNER, nullable=True),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_manually", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("extra_id", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
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
            "case_id IS NULL OR contact_id IS NOT NULL",
            name=op.f("ck_vault_entries_case_needs_contact"),
        ),
        sa.ForeignKeyConstraint(
            ["system_id", "tenant_id"],
            ["vault_systems.id", "vault_systems.tenant_id"],
            name="fk_vault_entries_system_id_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "tenant_id"],
            ["contacts.id", "contacts.tenant_id"],
            name="fk_vault_entries_contact_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name="fk_vault_entries_case_id_same_tenant",
            ondelete="SET NULL (case_id)",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_vault_entries_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_vault_entries_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vault_entries")),
    )
    op.create_index("ix_vault_entries_contact_id", "vault_entries", ["contact_id"])
    op.create_index(op.f("ix_vault_entries_tenant_id"), "vault_entries", ["tenant_id"])

    enable_tenant_rls("vault_systems")
    enable_tenant_rls("vault_entries")

    conn = op.get_bind()
    for order, (name, kind) in enumerate(DEFAULT_SYSTEMS):
        conn.execute(
            sa.text(
                "INSERT INTO vault_systems (id, tenant_id, name, kind, sort_order) "
                "SELECT gen_random_uuid(), id, :name, CAST(:kind AS vault_system_kind), :order "
                "FROM tenants"
            ),
            {"name": name, "kind": kind, "order": order},
        )
    # The vault is part of the core CRM: workspaces that keep cases get it. Employees get
    # it only when their specialist gives it to them.
    op.execute(
        "UPDATE tenants SET modules = array_append(modules, 'vault') "
        "WHERE 'cases' = ANY(modules) AND NOT 'vault' = ANY(modules)"
    )


def downgrade() -> None:
    op.execute("UPDATE users SET modules = array_remove(modules, 'vault')")
    op.execute("UPDATE tenants SET modules = array_remove(modules, 'vault')")
    op.drop_table("vault_entries")
    op.drop_table("vault_systems")
    SYSTEM_KIND.drop(op.get_bind())
    # Postgres cannot drop an enum value: rebuild the type without it.
    op.execute(
        "UPDATE visa_cases SET application_email_owner = 'specialist' "
        "WHERE application_email_owner = 'specialist_backup'"
    )
    op.execute("ALTER TYPE email_owner RENAME TO email_owner_old")
    op.execute("CREATE TYPE email_owner AS ENUM ('client', 'specialist', 'registrar')")
    op.execute(
        "ALTER TABLE visa_cases ALTER COLUMN application_email_owner TYPE email_owner "
        "USING application_email_owner::text::email_owner"
    )
    op.execute("DROP TYPE email_owner_old")

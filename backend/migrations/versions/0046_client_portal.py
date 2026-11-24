"""client portal of a case: link, client sessions, files at checklist items, data notice

Backlog 2.9 (docs/decisions.md → «Портал клиента и вход по коду»). A client opens the
link of their case, enters a one-time code and uploads documents to the items of the
case's checklist; the specialist accepts or returns each file. Before the first upload the
client acknowledges a data notice and, apart from it, consents to special categories of
data; both are kept as the proof (portal_notices, portal_consents), and neither is ever
changed: a trigger refuses UPDATE (a deleted card still takes them along).

Every table is a workspace table under tenant_isolation, with children referencing their
parents by (id, tenant_id), and the tables with a case or a client under employee_scope:
an employee sees the portal of the cases given to them only. The workspace gets the
specialist's details as the data controller, named in the notice.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE_CHANNEL = postgresql.ENUM(
    "email", "specialist", "telegram", name="code_channel", create_type=False
)
FILE_SOURCE = postgresql.ENUM("client", "staff", name="package_file_source", create_type=False)
FILE_STATUS = postgresql.ENUM(
    "pending", "accepted", "returned", name="package_file_status", create_type=False
)
RETURN_REASON = postgresql.ENUM(
    "unreadable", "need_fresh", "other", name="package_file_return_reason", create_type=False
)
GIVEN_BY = postgresql.ENUM(
    "applicant", "representative", name="consent_given_by", create_type=False
)
ENUMS = (CODE_CHANNEL, FILE_SOURCE, FILE_STATUS, RETURN_REASON, GIVEN_BY)

EMPLOYEE_SCOPE = {
    "client_links": "employee_sees_case(case_id)",
    "client_sessions": "employee_sees_case(case_id)",
    "package_files": "employee_sees_case(case_id)",
    "portal_consents": "employee_sees_contact(contact_id)",
}
UNCHANGEABLE = ("portal_notices", "portal_consents")


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def same_tenant(table: str, column: str, parent: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "tenant_id"],
        [f"{parent}.id", f"{parent}.tenant_id"],
        name=f"fk_{table}_{column}_same_tenant",
        ondelete=ondelete,
    )


def account(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "tenant_id"],
        ["users.id", "users.tenant_id"],
        name=f"fk_{table}_{column}_same_tenant",
        ondelete=f"SET NULL ({column})",
    )


def tenant(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="CASCADE",
    )


def moment(name: str, *, now: bool = False, nullable: bool = False) -> sa.Column:
    default = sa.text("now()") if now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=nullable)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)

    for column, kind in (
        ("controller_name", sa.String(200)),
        ("controller_address", sa.String(300)),
        ("controller_email", sa.String(320)),
        ("supervisory_authority", sa.String(300)),
    ):
        op.add_column("tenants", sa.Column(column, kind, nullable=True))

    op.create_table(
        "client_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("token_sealed", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        moment("created_at", now=True),
        moment("revoked_at", nullable=True),
        sa.Column("code_hash", sa.LargeBinary(), nullable=True),
        sa.Column("code_channel", CODE_CHANNEL, nullable=True),
        moment("code_sent_at", nullable=True),
        sa.Column("code_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("code_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("code_issued_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(code_hash IS NULL) = (code_sealed IS NULL)",
            name=op.f("ck_client_links_code_kept_both_ways"),
        ),
        same_tenant("client_links", "case_id", "visa_cases", "CASCADE"),
        account("client_links", "created_by"),
        account("client_links", "code_issued_by"),
        tenant("client_links"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_links")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_client_links_id_tenant"),
        sa.UniqueConstraint("token_hash", name=op.f("uq_client_links_token_hash")),
    )
    op.create_index(op.f("ix_client_links_tenant_id"), "client_links", ["tenant_id"])
    op.create_index(
        "uq_client_links_live_case",
        "client_links",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "client_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("code_channel", CODE_CHANNEL, nullable=False),
        moment("created_at", now=True),
        moment("last_seen_at", now=True),
        same_tenant("client_sessions", "link_id", "client_links", "CASCADE"),
        same_tenant("client_sessions", "case_id", "visa_cases", "CASCADE"),
        tenant("client_sessions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_client_sessions_token_hash")),
    )
    op.create_index(op.f("ix_client_sessions_tenant_id"), "client_sessions", ["tenant_id"])
    op.create_index(op.f("ix_client_sessions_link_id"), "client_sessions", ["link_id"])

    op.create_table(
        "package_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("checklist_item_id", sa.Uuid(), nullable=True),
        sa.Column("own_item_id", sa.Uuid(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source", FILE_SOURCE, nullable=False),
        sa.Column("status", FILE_STATUS, nullable=False),
        sa.Column("return_reason", RETURN_REASON, nullable=True),
        sa.Column("return_note", sa.String(300), nullable=True),
        sa.Column("link_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        moment("reviewed_at", nullable=True),
        moment("created_at", now=True),
        sa.CheckConstraint(
            "(checklist_item_id IS NULL) <> (own_item_id IS NULL)",
            name=op.f("ck_package_files_one_item"),
        ),
        sa.CheckConstraint(
            "(status = 'returned') = (return_reason IS NOT NULL)",
            name=op.f("ck_package_files_reason_when_returned"),
        ),
        sa.CheckConstraint(
            "return_reason <> 'other' OR return_note IS NOT NULL",
            name=op.f("ck_package_files_other_noted"),
        ),
        same_tenant("package_files", "case_id", "visa_cases", "CASCADE"),
        same_tenant("package_files", "own_item_id", "own_checklist_items", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["link_id", "tenant_id"],
            ["client_links.id", "client_links.tenant_id"],
            name="fk_package_files_link_id_same_tenant",
            ondelete="SET NULL (link_id)",
        ),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"],
            ["checklist_items.id"],
            name=op.f("fk_package_files_checklist_item_id_checklist_items"),
            ondelete="RESTRICT",
        ),
        account("package_files", "uploaded_by"),
        account("package_files", "reviewed_by"),
        tenant("package_files"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_files")),
    )
    op.create_index(op.f("ix_package_files_tenant_id"), "package_files", ["tenant_id"])
    op.create_index("ix_package_files_case_created", "package_files", ["case_id", "created_at"])

    op.create_table(
        "portal_notices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        moment("created_at", now=True),
        tenant("portal_notices"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_notices")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_portal_notices_id_tenant"),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_portal_notices_tenant_text"),
    )

    op.create_table(
        "portal_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=True),
        sa.Column("notice_id", sa.Uuid(), nullable=False),
        sa.Column("special_categories", sa.Boolean(), nullable=False),
        sa.Column("given_by", GIVEN_BY, nullable=False),
        sa.Column("representative_name", sa.String(200), nullable=True),
        sa.Column("code_channel", CODE_CHANNEL, nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        moment("given_at", now=True),
        sa.CheckConstraint(
            "(given_by = 'representative') = (representative_name IS NOT NULL)",
            name=op.f("ck_portal_consents_representative_named"),
        ),
        same_tenant("portal_consents", "contact_id", "contacts", "CASCADE"),
        same_tenant("portal_consents", "case_id", "visa_cases", "CASCADE"),
        same_tenant("portal_consents", "notice_id", "portal_notices", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["link_id", "tenant_id"],
            ["client_links.id", "client_links.tenant_id"],
            name="fk_portal_consents_link_id_same_tenant",
            ondelete="SET NULL (link_id)",
        ),
        tenant("portal_consents"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_consents")),
    )
    op.create_index(op.f("ix_portal_consents_tenant_id"), "portal_consents", ["tenant_id"])
    op.create_index(op.f("ix_portal_consents_case_id"), "portal_consents", ["case_id"])
    op.create_index(
        "ix_portal_consents_contact_given", "portal_consents", ["contact_id", "given_at"]
    )

    for table in ("client_links", "client_sessions", "package_files", *UNCHANGEABLE):
        enable_tenant_rls(table)
    for table, rule in EMPLOYEE_SCOPE.items():
        op.execute(
            f"CREATE POLICY employee_scope ON {table} AS RESTRICTIVE "
            f"USING (app_employee_id() IS NULL OR ({rule})) "
            f"WITH CHECK (app_employee_id() IS NULL OR ({rule}))"
        )

    # The proof of what a client was shown and marked is never changed; deleting a card
    # or a workspace still takes it along (the foreign keys), so DELETE stays allowed.
    op.execute(
        """
        CREATE FUNCTION portal_proof_unchanged() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are never changed', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for table in UNCHANGEABLE:
        op.execute(
            f"CREATE TRIGGER {table}_unchanged BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION portal_proof_unchanged()"
        )


def downgrade() -> None:
    for table in ("portal_consents", "portal_notices", "package_files", "client_sessions"):
        op.drop_table(table)
    op.drop_table("client_links")
    op.execute("DROP FUNCTION portal_proof_unchanged()")
    for column in (
        "supervisory_authority",
        "controller_email",
        "controller_address",
        "controller_name",
    ):
        op.drop_column("tenants", column)
    bind = op.get_bind()
    for enum in ENUMS:
        enum.drop(bind)

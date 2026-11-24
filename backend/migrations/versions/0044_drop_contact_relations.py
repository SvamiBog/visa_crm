"""clients are no longer linked to each other: the links table goes

The owner's decision of 2026-11-24 (backlog 1.15.11, docs/decisions.md → «Пилот: правки
визовика (2026-11-24)»): no family links between clients at all, not in the card and not
when a relative is added to a case. The specialist notes who is who in the client's notes
if she needs to; a family filed together stays a submission group of their cases, which
this migration does not touch.

Upgrade drops contact_relations with its rows, indexes and both policies (tenant_isolation
and employee_scope). The links kept in production are lost: that is the owner's decision.
The log keeps its old «contacts.linked» and «contacts.unlinked» entries.

Downgrade brings the table back as 0012 and 0031 made it, with the same keys, indexes and
policies, but empty: the dropped links cannot be restored.

Deploy: the previous version reads the table for every card it opens. While it still serves
during the deploy (from this migration until the new version takes the traffic) such a card
answers 500 and opens after a reload. A rollback of the code alone (docs/deploy.md) needs
`alembic downgrade 0043` too, or no card opens.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
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


# An employee saw a link only between two clients they see (0031).
EMPLOYEE_SEES = "employee_sees_contact(contact_id) AND employee_sees_contact(related_id)"


def upgrade() -> None:
    # The policies, indexes and keys go with the table.
    op.drop_table("contact_relations")


def downgrade() -> None:
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
    enable_tenant_rls("contact_relations")
    op.execute(
        "CREATE POLICY employee_scope ON contact_relations AS RESTRICTIVE "
        f"USING (app_employee_id() IS NULL OR ({EMPLOYEE_SEES})) "
        f"WITH CHECK (app_employee_id() IS NULL OR ({EMPLOYEE_SEES}))"
    )

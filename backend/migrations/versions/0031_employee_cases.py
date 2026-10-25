"""employees see only the cases a specialist gave them

An employee sees those cases, the rest of their submission groups, the clients of all of
them, and the clients and cases the employee created: the owner's answer to question 17
(docs/decisions.md → «Сотрудники: только свои кейсы»). The table employee_cases holds the
grants. The app puts the employee's id in app.employee_id (app/core/db.py); a restrictive
policy employee_scope on every table with client data then lets through only what the
employee may see, on top of tenant_isolation, so a query that forgets the rule finds
nothing. Specialists' sessions leave app.employee_id empty and see the whole workspace.

The visibility functions run as their owner (SECURITY DEFINER): they read visa_cases and
employee_cases past the policies they serve, which would otherwise recurse. They check only
the current workspace. The same goes for the next case number and the phone check, which
must count cases and contacts the employee does not see.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
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


TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"

# Tables with client data and who sees a row: the policy applies only in an employee's
# session (app_employee_id() is set). Writes are held to the same rule, unless a pair
# gives (who sees, who writes).
EMPLOYEE_SCOPE: dict[str, str | tuple[str, str]] = {
    "contacts": "created_by = app_employee_id() OR employee_sees_contact(id)",
    "visa_cases": "created_by = app_employee_id() OR employee_sees_case(id)",
    "consultations": "employee_sees_contact(contact_id)",
    "contact_notes": "employee_sees_contact(contact_id)",
    "contact_documents": "employee_sees_contact(contact_id)",
    "contact_templates": "employee_sees_contact(contact_id)",
    "trips": "employee_sees_contact(contact_id)",
    "contact_relations": (
        "employee_sees_contact(contact_id) AND employee_sees_contact(related_id)"
    ),
    # The specialist's own accounts (no client) stay visible with the vault module.
    "vault_entries": "contact_id IS NULL OR employee_sees_contact(contact_id)",
    "case_finances": "employee_sees_case(case_id)",
    "case_payments": "employee_sees_case(case_id)",
    "case_expenses": "employee_sees_case(case_id)",
    "waiting_list_entries": "employee_sees_case(case_id)",
    "application_attempts": "employee_sees_case(case_id)",
    "bookings": "employee_sees_case(case_id)",
    "package_items": "employee_sees_case(case_id)",
    # Their own grants, read-only: only the specialist gives cases.
    "employee_cases": ("user_id = app_employee_id()", "false"),
    # The specialist's own: the balance with registrars and imported tables.
    "registrar_payments": "false",
    "import_mappings": "false",
    "import_runs": "false",
    "import_rows": "false",
}

FUNCTIONS = (
    """
CREATE FUNCTION app_employee_id() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.employee_id', true), '')::uuid
$$
""",
    f"""
CREATE FUNCTION employee_sees_case(target uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
        SELECT 1 FROM visa_cases c
        WHERE c.id = target AND c.tenant_id = {TENANT}
          AND (
            c.created_by = app_employee_id()
            OR EXISTS (
                SELECT 1 FROM employee_cases g
                JOIN visa_cases given ON given.id = g.case_id
                WHERE g.user_id = app_employee_id()
                  AND (given.id = c.id OR given.group_id = c.group_id)
            )
          )
    )
$$
""",
    f"""
CREATE FUNCTION employee_sees_contact(target uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
        SELECT 1 FROM contacts ct
        WHERE ct.id = target AND ct.tenant_id = {TENANT}
          AND ct.created_by = app_employee_id()
    ) OR EXISTS (
        SELECT 1 FROM visa_cases c
        WHERE c.contact_id = target AND c.tenant_id = {TENANT} AND employee_sees_case(c.id)
    )
$$
""",
    f"""
CREATE FUNCTION last_case_number() RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT coalesce(max(number), 0) FROM visa_cases WHERE tenant_id = {TENANT}
$$
""",
    f"""
CREATE FUNCTION contact_phone_taken(digits text, exclude uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
        SELECT 1 FROM contacts
        WHERE tenant_id = {TENANT} AND phone = digits
          AND (exclude IS NULL OR id <> exclude)
    )
$$
""",
)


def upgrade() -> None:
    op.create_unique_constraint("uq_users_id_tenant", "users", ["id", "tenant_id"])
    op.create_table(
        "employee_cases",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_employee_cases_user_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["visa_cases.id", "visa_cases.tenant_id"],
            name="fk_employee_cases_case_id_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_employee_cases_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_employee_cases_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "case_id", name=op.f("pk_employee_cases")),
    )
    op.create_index("ix_employee_cases_case_id", "employee_cases", ["case_id"])
    op.create_index(op.f("ix_employee_cases_tenant_id"), "employee_cases", ["tenant_id"])
    enable_tenant_rls("employee_cases")

    for function in FUNCTIONS:
        op.execute(function)
    for table, rule in EMPLOYEE_SCOPE.items():
        sees, writes = rule if isinstance(rule, tuple) else (rule, rule)
        op.execute(
            f"CREATE POLICY employee_scope ON {table} AS RESTRICTIVE "
            f"USING (app_employee_id() IS NULL OR ({sees})) "
            f"WITH CHECK (app_employee_id() IS NULL OR ({writes}))"
        )


def downgrade() -> None:
    for table in EMPLOYEE_SCOPE:
        op.execute(f"DROP POLICY employee_scope ON {table}")
    for function in (
        "contact_phone_taken(text, uuid)",
        "last_case_number()",
        "employee_sees_contact(uuid)",
        "employee_sees_case(uuid)",
        "app_employee_id()",
    ):
        op.execute(f"DROP FUNCTION {function}")
    op.drop_table("employee_cases")
    op.drop_constraint("uq_users_id_tenant", "users", type_="unique")

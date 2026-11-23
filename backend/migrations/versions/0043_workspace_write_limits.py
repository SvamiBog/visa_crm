"""the database holds what only the admin, or only the specialist, may change

Security audit run-1 (backlog S20; h06.1, h09.1, h09.2, h07.1, h06.3, h04.2, h05.1). These
rules were kept by the API alone; the database now holds them too, for a query the API
never sends:

- A workspace's modules, subscription (tariff, start, trial) and block change only in the
  system scope (the admin). An employee changes no setting of the workspace; they may only
  make its vault key the first time a secret is saved. A trigger checks it: a policy cannot
  compare a row with what it was.
- A workspace neither adds nor removes workspaces.
- Accounts: a workspace writes only the accounts of its employees, never its owner's, and no
  role but employee; an employee writes none. An employee still reads every account of the
  workspace: notes and documents show their authors (owner's answer, 2026-11-23).
- Sessions and recovery codes: only the system scope (login, settings) makes or changes
  one. The specialist's scope still reaches the rows of the workspace's accounts (0007:
  she signs out an employee she blocks); an employee's, only their own.
- An employee does not see the subscription payments, and reads but does not write the
  specialist's remarks on visa centers (owner's answers, 2026-11-23).
- The SECURITY DEFINER functions of 0031 read past the policies: only the app role runs them.
- A row names an account of its own workspace, (column, tenant_id) → users(id, tenant_id):
  Postgres checks foreign keys past Row-Level Security. Removing the account clears only
  the column.
- The unique keys of partner terms, registrar prices and visa center cities start with
  tenant_id: an insert naming another workspace's parent then fails on the foreign key
  whether or not that parent has such a row, and tells nothing about it.

Deploy: the tables are small and the statements take moments, but dropping and adding the
foreign keys holds `users` and the tables above locked until the migration commits. If the
running version holds one of them longer (an import's transaction), the migration gives up
after lock_timeout and the deploy is retried, instead of every request queueing behind it.

Downgrade restores the policies, keys and grants of 0042; no data is lost.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept inline (not imported from app code) so this migration never changes after merge.
APP_ROLE = "visacrm_app"
SYSTEM = "current_setting('app.system', true) = 'on'"
SPECIALIST = "app_employee_id() IS NULL"
LOCK_TIMEOUT = "10s"

# Admin panel or a workspace's employees, never an employee's session.
EMPLOYEE_ACCOUNTS = f"{SYSTEM} OR ({SPECIALIST} AND role = 'employee')"
# On top of own_users (0007), which keeps a workspace to the rows of its own accounts.
SIGN_IN_ROWS = f"{SYSTEM} OR {SPECIALIST} OR user_id = app_employee_id()"

TENANT_TRIGGER = """
CREATE FUNCTION tenant_changes_allowed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- The admin panel (system scope) and the database owner (migrations) are no workspace.
    IF current_setting('app.system', true) = 'on'
       OR nullif(current_setting('app.tenant_id', true), '') IS NULL THEN
        RETURN NEW;
    END IF;
    IF (NEW.modules, NEW.tariff_id, NEW.subscription_starts_on, NEW.trial_until, NEW.is_active)
       IS DISTINCT FROM
       (OLD.modules, OLD.tariff_id, OLD.subscription_starts_on, OLD.trial_until, OLD.is_active)
    THEN
        RAISE EXCEPTION 'only the admin sets the modules, subscription and block of a workspace'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF app_employee_id() IS NOT NULL AND (
        to_jsonb(NEW) - 'vault_key_encrypted' IS DISTINCT FROM to_jsonb(OLD) - 'vault_key_encrypted'
        OR (OLD.vault_key_encrypted IS NOT NULL
            AND NEW.vault_key_encrypted IS DISTINCT FROM OLD.vault_key_encrypted)
    ) THEN
        RAISE EXCEPTION 'only the specialist changes the settings of a workspace'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$
"""

DEFINER_FUNCTIONS = (
    "employee_sees_case(uuid)",
    "employee_sees_contact(uuid)",
    "last_case_number()",
    "contact_phone_taken(text, uuid)",
)

# Columns naming the account that made or changed a row.
ACCOUNT_COLUMNS = (
    ("contacts", "created_by"),
    ("contact_notes", "author_id"),
    ("contact_documents", "uploaded_by"),
    ("consultations", "created_by"),
    ("visa_cases", "created_by"),
    ("employee_cases", "granted_by"),
    ("waiting_list_entries", "created_by"),
    ("application_attempts", "created_by"),
    ("vault_entries", "created_by"),
    ("bookings", "created_by"),
    ("case_payments", "created_by"),
    ("case_expenses", "created_by"),
    ("registrar_payments", "created_by"),
    ("reminder_dismissals", "dismissed_by"),
    ("visa_center_notes", "updated_by"),
    ("import_runs", "created_by"),
    ("trips", "created_by"),
    ("document_templates", "uploaded_by"),
    ("contact_templates", "created_by"),
)

# Unique keys of child dictionaries: name, columns after tenant_id, NULLS NOT DISTINCT.
UNIQUE_KEYS = (
    ("partner_terms", "uq_partner_terms_partner_country", ["partner_id", "country_id"], True),
    (
        "registrar_prices",
        "uq_registrar_prices_key",
        ["registrar_id", "country_id", "purpose_id", "hall"],
        True,
    ),
    ("visa_centers", "uq_visa_centers_rule_city", ["rule_id", "city_ru"], False),
)


def restrictive(table: str, name: str, command: str, rule: str) -> None:
    using = "" if command == "INSERT" else f" USING ({rule})"
    check = "" if command == "DELETE" else f" WITH CHECK ({rule})"
    op.execute(f"CREATE POLICY {name} ON {table} AS RESTRICTIVE FOR {command}{using}{check}")


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    op.execute(TENANT_TRIGGER)
    op.execute(
        "CREATE TRIGGER tenant_changes_allowed BEFORE UPDATE ON tenants "
        "FOR EACH ROW EXECUTE FUNCTION tenant_changes_allowed()"
    )
    for command in ("INSERT", "DELETE"):
        restrictive("tenants", f"system_{command.lower()}", command, SYSTEM)

    for command in ("INSERT", "UPDATE", "DELETE"):
        restrictive("users", f"employee_accounts_{command.lower()}", command, EMPLOYEE_ACCOUNTS)
    for table in ("user_sessions", "user_recovery_codes"):
        op.execute(
            f"CREATE POLICY sign_in_scope ON {table} AS RESTRICTIVE "
            f"USING ({SIGN_IN_ROWS}) WITH CHECK ({SYSTEM})"
        )

    # As employee_scope in 0031: in an employee's session the rows are not there.
    op.execute(
        "CREATE POLICY employee_scope ON subscription_payments AS RESTRICTIVE "
        "USING (app_employee_id() IS NULL OR (false)) "
        "WITH CHECK (app_employee_id() IS NULL OR (false))"
    )
    for command in ("INSERT", "UPDATE", "DELETE"):
        restrictive("visa_center_notes", f"specialist_{command.lower()}", command, SPECIALIST)

    for function in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {APP_ROLE}")

    for table, column in ACCOUNT_COLUMNS:
        op.drop_constraint(f"fk_{table}_{column}_users", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_same_tenant",
            table,
            "users",
            [column, "tenant_id"],
            ["id", "tenant_id"],
            ondelete=f"SET NULL ({column})",
        )

    for table, name, columns, nulls_not_distinct in UNIQUE_KEYS:
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(
            name, table, ["tenant_id", *columns], postgresql_nulls_not_distinct=nulls_not_distinct
        )

    # Back to the session's own value: a later migration in the same run waits as it likes.
    op.execute("SET LOCAL lock_timeout TO DEFAULT")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    for table, name, columns, nulls_not_distinct in reversed(UNIQUE_KEYS):
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(
            name, table, columns, postgresql_nulls_not_distinct=nulls_not_distinct
        )

    for table, column in reversed(ACCOUNT_COLUMNS):
        op.drop_constraint(f"fk_{table}_{column}_same_tenant", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
        )

    for function in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM {APP_ROLE}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")

    for command in ("insert", "update", "delete"):
        op.execute(f"DROP POLICY specialist_{command} ON visa_center_notes")
    op.execute("DROP POLICY employee_scope ON subscription_payments")
    for table in ("user_sessions", "user_recovery_codes"):
        op.execute(f"DROP POLICY sign_in_scope ON {table}")
    for command in ("insert", "update", "delete"):
        op.execute(f"DROP POLICY employee_accounts_{command} ON users")

    for command in ("insert", "delete"):
        op.execute(f"DROP POLICY system_{command} ON tenants")
    op.execute("DROP TRIGGER tenant_changes_allowed ON tenants")
    op.execute("DROP FUNCTION tenant_changes_allowed()")

    op.execute("SET LOCAL lock_timeout TO DEFAULT")

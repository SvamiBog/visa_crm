"""the passport's data is removed a week after the decision

Backlog 2.8, owner's decision 2026-11-24 (docs/decisions.md → «Удаление паспортных данных
после решения»). Rules: app/core/passport_purge.py.

- visa_cases.decision_entered_at: when the result or its date was last entered in the CRM.
  A trigger sets it on every path that writes a result (the case form, the family, the
  import), so none is missed. An imported result has no date of decision: its week counts
  from this day, and no passport goes sooner than two days after it is entered. Existing
  results: the day of this release.
- contacts.passport_kept_until: the passport stays until this day at least («Отложить на
  30 дней», or the first run).
- contacts.passport_notice_from: the first run's list shows from this day.

First run: nothing is removed at the deploy. A client already due (every case at the
result stage, a decision, passport data) is kept until the release day + 14 in the
specialist's time zone, and «Требует внимания» lists them from the release day.

Downgrade: the columns and the trigger go; nothing removed comes back.

- passport_purge_cases(contacts): the result facts of every case of the given clients of
  the workspace. An employee sees only the cases given to them (employee_scope), yet a
  client's other case still in work must keep the passport and a later decision must
  count: the function reads past that policy (SECURITY DEFINER, only for the app role,
  inside the session's workspace) and gives back stages and dates, never client data.

Both tables already have RLS; the new columns change no policy. The trigger function runs
as the caller (not SECURITY DEFINER).

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# app/core/passport_purge.py → PURGED_FIELDS; copied, a migration does not import the app.
PURGED_FIELDS = (
    "passport_number",
    "personal_number",
    "passport_issued_on",
    "passport_expires_on",
    "passport_issued_by",
    "birth_place",
)
FIRST_RUN_DAYS = 14
APP_ROLE = "visacrm_app"
TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
CASES_FUNCTION = "passport_purge_cases(uuid[])"
_HAS_PASSPORT = " OR ".join(f"c.questionnaire->>'{field}' IS NOT NULL" for field in PURGED_FIELDS)
# Every client already due is kept until the release + 14 days and listed from today.
# A module constant: tests/test_passport_purge.py runs it against a workspace.
FIRST_RUN = f"""
    UPDATE contacts c
    SET passport_kept_until = (now() AT TIME ZONE t.timezone)::date + {FIRST_RUN_DAYS},
        passport_notice_from = (now() AT TIME ZONE t.timezone)::date
    FROM tenants t
    WHERE t.id = c.tenant_id
      AND ({_HAS_PASSPORT})
      AND EXISTS (
          SELECT FROM visa_cases v WHERE v.contact_id = c.id AND v.outcome IS NOT NULL
      )
      AND NOT EXISTS (
          SELECT FROM visa_cases v WHERE v.contact_id = c.id AND v.stage <> 'result'
      )
"""


def upgrade() -> None:
    op.add_column(
        "visa_cases", sa.Column("decision_entered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("contacts", sa.Column("passport_kept_until", sa.Date(), nullable=True))
    op.add_column("contacts", sa.Column("passport_notice_from", sa.Date(), nullable=True))

    # The app cannot set the column itself: the trigger keeps the old value unless the
    # result or its date changes.
    op.execute(
        """
        CREATE FUNCTION visa_case_decision_entered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.outcome IS NULL THEN
                NEW.decision_entered_at := NULL;
            ELSIF TG_OP = 'INSERT'
                OR OLD.outcome IS DISTINCT FROM NEW.outcome
                OR OLD.decided_on IS DISTINCT FROM NEW.decided_on THEN
                NEW.decision_entered_at := now();
            ELSE
                NEW.decision_entered_at := OLD.decision_entered_at;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER visa_cases_decision_entered BEFORE INSERT OR UPDATE ON visa_cases "
        "FOR EACH ROW EXECUTE FUNCTION visa_case_decision_entered()"
    )
    # Existing results were entered at the latest today. Direct UPDATE of the column: the
    # trigger would keep it NULL, so it is set with the trigger off for this statement.
    op.execute("ALTER TABLE visa_cases DISABLE TRIGGER visa_cases_decision_entered")
    op.execute("UPDATE visa_cases SET decision_entered_at = now() WHERE outcome IS NOT NULL")
    op.execute("ALTER TABLE visa_cases ENABLE TRIGGER visa_cases_decision_entered")

    op.execute(
        f"""
        CREATE FUNCTION passport_purge_cases(targets uuid[])
        RETURNS TABLE (
            contact_id uuid, stage text, outcome text, decided_on date,
            decision_entered_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT v.contact_id, v.stage::text, v.outcome::text, v.decided_on,
                   v.decision_entered_at
            FROM visa_cases v
            WHERE v.tenant_id = {TENANT} AND v.contact_id = ANY(targets)
        $$
        """
    )
    op.execute(f"REVOKE EXECUTE ON FUNCTION {CASES_FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {CASES_FUNCTION} TO {APP_ROLE}")

    op.execute(FIRST_RUN)


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {CASES_FUNCTION}")
    op.execute("DROP TRIGGER visa_cases_decision_entered ON visa_cases")
    op.execute("DROP FUNCTION visa_case_decision_entered()")
    op.drop_column("contacts", "passport_notice_from")
    op.drop_column("contacts", "passport_kept_until")
    op.drop_column("visa_cases", "decision_entered_at")

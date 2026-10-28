"""directions are the workspace's own: rules «destination × submission country» and cities

Backlog 1.20 (docs/decisions.md → «Свои направления визовика»): every specialist works her
own countries and cities, so she sets them up herself; the admin keeps only countries and
operators. The shared rules and cities become workspace rows under Row-Level Security, a
city points to its direction and a case to its city within one workspace (composite keys).

Existing data: each workspace gets a copy of the rules for the pairs its cases use, with
their cities; cases and remarks move to the copies; the shared rows are then removed. A
workspace without cases starts empty. «Проверено» goes: a workspace's own rule needs no
check. The English name of a city becomes optional.

Downgrade: the reference is shared again. Of the workspaces' copies of a pair the first
one (by id) stays, the other copies' settings are lost; their cities join it, cases keep
their city.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("country_rules", "visa_centers")
SYSTEM = "current_setting('app.system', true) = 'on'"
ANY_SCOPE = f"{SYSTEM} OR nullif(current_setting('app.tenant_id', true), '') IS NOT NULL"


def enable_tenant_rls(table: str) -> None:
    """Rows are visible only to their tenant (app.tenant_id) or to the system scope."""
    rule = (
        "current_setting('app.system', true) = 'on' "
        "OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({rule}) WITH CHECK ({rule})")


def enable_shared_rls(table: str) -> None:
    """Shared reference: every scope reads it, only the system scope (admin) writes."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read_any_scope ON {table} FOR SELECT USING ({ANY_SCOPE})")
    for command in ("INSERT", "UPDATE", "DELETE"):
        using = "" if command == "INSERT" else f" USING ({SYSTEM})"
        check = "" if command == "DELETE" else f" WITH CHECK ({SYSTEM})"
        op.execute(f"CREATE POLICY system_{command.lower()} ON {table} FOR {command}{using}{check}")


def drop_shared_rls(table: str) -> None:
    for policy in ("read_any_scope", "system_insert", "system_update", "system_delete"):
        op.execute(f"DROP POLICY {policy} ON {table}")


def upgrade() -> None:
    # The migration runs as the database owner, outside the app role: RLS does not apply.
    for table in TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.drop_constraint("uq_country_rules_pair", "country_rules", type_="unique")
    op.drop_constraint("fk_visa_centers_rule_id_country_rules", "visa_centers", type_="foreignkey")
    op.drop_constraint(
        "fk_visa_cases_visa_center_id_visa_centers", "visa_cases", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_visa_center_notes_visa_center_id_visa_centers", "visa_center_notes", type_="foreignkey"
    )

    # A copy of each shared rule for every workspace whose cases use its pair.
    op.execute(
        "CREATE TEMP TABLE rule_copies AS SELECT pairs.*, gen_random_uuid() AS new_id FROM ("
        " SELECT DISTINCT c.tenant_id, r.id AS old_id FROM visa_cases c"
        " JOIN country_rules r ON r.destination_id = c.destination_id"
        " AND r.submission_id = c.submission_country_id) pairs"
    )
    op.execute(
        "INSERT INTO country_rules (id, tenant_id, destination_id, submission_id, booking_type, "
        "window_days, vac_operator_id, represented_by_id, identifier_type, appeals_accepted, "
        "appeal_deadline_days, notes, is_active) "
        "SELECT rc.new_id, rc.tenant_id, r.destination_id, r.submission_id, r.booking_type, "
        "r.window_days, r.vac_operator_id, r.represented_by_id, r.identifier_type, "
        "r.appeals_accepted, r.appeal_deadline_days, r.notes, r.is_active "
        "FROM rule_copies rc JOIN country_rules r ON r.id = rc.old_id"
    )
    op.execute(
        "CREATE TEMP TABLE center_copies AS SELECT rc.tenant_id, v.id AS old_id, "
        "rc.new_id AS rule_id, gen_random_uuid() AS new_id "
        "FROM rule_copies rc JOIN visa_centers v ON v.rule_id = rc.old_id"
    )
    op.execute(
        "INSERT INTO visa_centers (id, tenant_id, rule_id, city_ru, city_en, is_active) "
        "SELECT cc.new_id, cc.tenant_id, cc.rule_id, v.city_ru, v.city_en, v.is_active "
        "FROM center_copies cc JOIN visa_centers v ON v.id = cc.old_id"
    )
    for table in ("visa_cases", "visa_center_notes"):
        op.execute(
            f"UPDATE {table} t SET visa_center_id = cc.new_id FROM center_copies cc "
            "WHERE t.visa_center_id = cc.old_id AND t.tenant_id = cc.tenant_id"
        )
    # Remarks on a city whose pair no case of the workspace uses go with the shared row.
    op.execute(
        "DELETE FROM visa_center_notes n WHERE NOT EXISTS (SELECT 1 FROM visa_centers v "
        "WHERE v.id = n.visa_center_id AND v.tenant_id = n.tenant_id)"
    )
    op.execute("DELETE FROM visa_centers WHERE tenant_id IS NULL")
    op.execute("DELETE FROM country_rules WHERE tenant_id IS NULL")
    op.execute("DROP TABLE rule_copies, center_copies")

    for table in TABLES:
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_foreign_key(
            op.f(f"fk_{table}_tenant_id_tenants"),
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False)
        op.create_unique_constraint(f"uq_{table}_id_tenant", table, ["id", "tenant_id"])
    op.create_unique_constraint(
        "uq_country_rules_pair", "country_rules", ["tenant_id", "destination_id", "submission_id"]
    )
    op.drop_column("country_rules", "is_verified")
    op.alter_column("visa_centers", "city_en", existing_type=sa.String(length=100), nullable=True)
    op.create_foreign_key(
        "fk_visa_centers_rule_id_same_tenant",
        "visa_centers",
        "country_rules",
        ["rule_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_visa_cases_visa_center_id_same_tenant",
        "visa_cases",
        "visa_centers",
        ["visa_center_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_visa_center_notes_visa_center_id_same_tenant",
        "visa_center_notes",
        "visa_centers",
        ["visa_center_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="CASCADE",
    )
    for table in TABLES:
        drop_shared_rls(table)
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
    op.drop_constraint(
        "fk_visa_center_notes_visa_center_id_same_tenant", "visa_center_notes", type_="foreignkey"
    )
    op.drop_constraint("fk_visa_cases_visa_center_id_same_tenant", "visa_cases", type_="foreignkey")
    op.drop_constraint("fk_visa_centers_rule_id_same_tenant", "visa_centers", type_="foreignkey")

    # One rule per pair stays; each city goes to it once, cases and remarks follow the city.
    op.execute(
        "CREATE TEMP TABLE kept_rules AS SELECT DISTINCT ON (destination_id, submission_id) "
        "id, destination_id, submission_id FROM country_rules "
        "ORDER BY destination_id, submission_id, id"
    )
    op.execute(
        "CREATE TEMP TABLE center_map AS SELECT v.id AS old_id, k.id AS rule_id, "
        "first_value(v.id) OVER (PARTITION BY k.id, v.city_ru ORDER BY r.id = k.id DESC, v.id) "
        "AS new_id FROM visa_centers v JOIN country_rules r ON r.id = v.rule_id "
        "JOIN kept_rules k ON k.destination_id = r.destination_id "
        "AND k.submission_id = r.submission_id"
    )
    for table in ("visa_cases", "visa_center_notes"):
        op.execute(
            f"UPDATE {table} t SET visa_center_id = m.new_id FROM center_map m "
            "WHERE t.visa_center_id = m.old_id AND m.old_id <> m.new_id"
        )
    op.execute(
        "UPDATE visa_centers v SET rule_id = m.rule_id FROM center_map m "
        "WHERE v.id = m.old_id AND m.old_id = m.new_id"
    )
    op.execute(
        "DELETE FROM visa_centers v USING center_map m WHERE v.id = m.old_id "
        "AND m.old_id <> m.new_id"
    )
    op.execute("DELETE FROM country_rules r WHERE r.id NOT IN (SELECT id FROM kept_rules)")
    op.execute("DROP TABLE kept_rules, center_map")
    op.execute("UPDATE visa_centers SET city_en = city_ru WHERE city_en IS NULL")

    op.create_foreign_key(
        "fk_visa_centers_rule_id_country_rules",
        "visa_centers",
        "country_rules",
        ["rule_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_visa_cases_visa_center_id_visa_centers",
        "visa_cases",
        "visa_centers",
        ["visa_center_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_visa_center_notes_visa_center_id_visa_centers",
        "visa_center_notes",
        "visa_centers",
        ["visa_center_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("visa_centers", "city_en", existing_type=sa.String(length=100), nullable=False)
    op.add_column(
        "country_rules",
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.drop_constraint("uq_country_rules_pair", "country_rules", type_="unique")
    op.create_unique_constraint(
        "uq_country_rules_pair", "country_rules", ["destination_id", "submission_id"]
    )
    for table in TABLES:
        op.drop_constraint(f"uq_{table}_id_tenant", table, type_="unique")
        op.drop_index(op.f(f"ix_{table}_tenant_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_tenant_id_tenants"), table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
        enable_shared_rls(table)

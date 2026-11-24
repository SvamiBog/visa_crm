"""Rules the database holds even for a query the API never sends (security audit run-1,
backlog S20, migration 0043): the fields only the admin sets, how far a workspace and an
employee reach into accounts and their sign-in rows, the subscription payments and the
remarks on visa centers (owner's answers of 2026-11-23), references to accounts and
parents that stay inside one workspace, and who may run the functions the policies use."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import scope_to_system, scope_to_tenant

A, B = uuid.UUID(int=0xA), uuid.UUID(int=0xB)
SPECIALIST_A, EMPLOYEE_A = uuid.UUID(int=0xA1), uuid.UUID(int=0xA2)
SPECIALIST_B = uuid.UUID(int=0xB1)
RULE_A, RULE_B = uuid.UUID(int=0xA3), uuid.UUID(int=0xB3)
CENTER_A, CENTER_A2, CENTER_B = uuid.UUID(int=0xA4), uuid.UUID(int=0xA5), uuid.UUID(int=0xB4)
PARTNER_B, REGISTRAR_B = uuid.UUID(int=0xB5), uuid.UUID(int=0xB6)

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def workspaces(owner_engine: AsyncEngine) -> None:
    """Anna's workspace A with her employee Olga, Boris's workspace B. Each account has a
    session and a recovery code; A has a subscription payment and a remark on one of its
    two Polish centers; B has a partner, a registrar and a center with their own keys."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:a, 'Anna'), (:b, 'Boris')"),
            {"a": A, "b": B},
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, password_hash) VALUES "
                "(:sa, :a, 'anna@example.com', 'specialist', 'hash-a'), "
                "(:ea, :a, 'olga@example.com', 'employee', 'hash-o'), "
                "(:sb, :b, 'boris@example.com', 'specialist', 'hash-b')"
            ),
            {"a": A, "b": B, "sa": SPECIALIST_A, "ea": EMPLOYEE_A, "sb": SPECIALIST_B},
        )
        for n, user in enumerate((SPECIALIST_A, EMPLOYEE_A, SPECIALIST_B)):
            await conn.execute(
                text(
                    "INSERT INTO user_sessions (id, user_id, token_hash, expires_at) "
                    "VALUES (gen_random_uuid(), :u, :h, now() + interval '1 day')"
                ),
                {"u": user, "h": f"{n:064d}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO user_recovery_codes (id, user_id, code_hash) "
                    "VALUES (gen_random_uuid(), :u, :h)"
                ),
                {"u": user, "h": f"{n:064d}"},
            )
        await conn.execute(
            text(
                "INSERT INTO subscription_payments (id, tenant_id, paid_on, amount, currency) "
                "VALUES (gen_random_uuid(), :a, DATE '2026-11-01', 30, 'EUR')"
            ),
            {"a": A},
        )
        await conn.execute(
            text("INSERT INTO tariffs (id, name, price, currency) VALUES (:id, 'Base', 30, 'EUR')"),
            {"id": uuid.uuid4()},
        )
        for tenant, rule in ((A, RULE_A), (B, RULE_B)):
            await conn.execute(
                text(
                    "INSERT INTO country_rules (id, tenant_id, destination_id, submission_id, "
                    "booking_type) VALUES (:id, :t, (SELECT id FROM countries WHERE code = 'PL'), "
                    "(SELECT id FROM countries WHERE code = 'BY'), 'waiting_list')"
                ),
                {"id": rule, "t": tenant},
            )
        await conn.execute(
            text(
                "INSERT INTO visa_centers (id, tenant_id, rule_id, city_ru) VALUES "
                "(:ca, :a, :ra, 'Минск'), (:ca2, :a, :ra, 'Гродно'), (:cb, :b, :rb, 'Минск')"
            ),
            {"a": A, "b": B, "ra": RULE_A, "rb": RULE_B, "ca": CENTER_A, "ca2": CENTER_A2}
            | {"cb": CENTER_B},
        )
        await conn.execute(
            text(
                "INSERT INTO visa_center_notes (id, tenant_id, visa_center_id, note, updated_by) "
                "VALUES (gen_random_uuid(), :a, :c, 'Капча', :sa)"
            ),
            {"a": A, "c": CENTER_A, "sa": SPECIALIST_A},
        )
        await conn.execute(
            text("INSERT INTO partners (id, tenant_id, name) VALUES (:p, :b, 'Пётр')"),
            {"p": PARTNER_B, "b": B},
        )
        await conn.execute(
            text(
                "INSERT INTO partner_terms (id, tenant_id, partner_id, country_id, terms_type) "
                "VALUES (gen_random_uuid(), :b, :p, NULL, 'none')"
            ),
            {"p": PARTNER_B, "b": B},
        )
        await conn.execute(
            text(
                "INSERT INTO registrars (id, tenant_id, name, kind, currency) "
                "VALUES (:r, :b, 'Талоны', 'company', 'BYN')"
            ),
            {"r": REGISTRAR_B, "b": B},
        )
        await conn.execute(
            text(
                "INSERT INTO registrar_prices (id, tenant_id, registrar_id, country_id, "
                "purpose_id, hall, amount, currency) VALUES (gen_random_uuid(), :b, :r, "
                "(SELECT id FROM countries WHERE code = 'PL'), NULL, 'standard', 10, 'BYN')"
            ),
            {"r": REGISTRAR_B, "b": B},
        )


def _scope(session: AsyncSession, who: str) -> None:
    if who == "system":
        scope_to_system(session)
    elif who == "specialist":
        scope_to_tenant(session, A)
    elif who == "employee":
        scope_to_tenant(session, A, employee_id=EMPLOYEE_A)
    else:
        raise ValueError(who)


async def changed(sessionmaker: Sessions, who: str, sql: str, **params: object) -> int:
    """Rows the statement changed in that scope, rolled back; DBAPIError if refused."""
    async with sessionmaker() as session:
        _scope(session, who)
        result = await session.execute(text(sql), params)
        count = result.rowcount
        await session.rollback()
        return count


async def rows(sessionmaker: Sessions, who: str, sql: str, **params: object) -> set:
    async with sessionmaker() as session:
        _scope(session, who)
        return set((await session.execute(text(sql), params)).scalars().all())


# --- Fields only the admin sets (h06.1, h09.1) -----------------------------------------

ADMIN_CHANGES = (
    "modules = '{clients}'",
    "is_active = false",
    "trial_until = DATE '2030-01-01'",
    "tariff_id = (SELECT id FROM tariffs), subscription_starts_on = DATE '2026-11-01'",
)


@pytest.mark.parametrize("change", ADMIN_CHANGES)
@pytest.mark.parametrize("who", ["specialist", "employee"])
@pytest.mark.usefixtures("workspaces")
async def test_a_workspace_cannot_set_what_only_the_admin_sets(
    sessionmaker: Sessions, who: str, change: str
) -> None:
    with pytest.raises(DBAPIError, match="only the admin"):
        await changed(sessionmaker, who, f"UPDATE tenants SET {change}")


@pytest.mark.usefixtures("workspaces")
async def test_the_admin_still_sets_them(sessionmaker: Sessions) -> None:
    for change in ADMIN_CHANGES:
        sql = f"UPDATE tenants SET {change} WHERE id = :a"
        assert await changed(sessionmaker, "system", sql, a=A) == 1


SETTINGS = (
    "name = 'Anna K.'",
    "tax_rate = 10",
    "timezone = 'Europe/Warsaw'",
    "payment_currencies = '{BYN,EUR}'",
    "reminder_settings = '{\"passport_months\": 3}'::jsonb",
    "max_deleted_case_number = 7",
)


@pytest.mark.parametrize("change", SETTINGS)
@pytest.mark.usefixtures("workspaces")
async def test_the_specialist_changes_her_settings_and_an_employee_does_not(
    sessionmaker: Sessions, change: str
) -> None:
    assert await changed(sessionmaker, "specialist", f"UPDATE tenants SET {change}") == 1
    with pytest.raises(DBAPIError, match="only the specialist"):
        await changed(sessionmaker, "employee", f"UPDATE tenants SET {change}")


@pytest.mark.usefixtures("workspaces")
async def test_an_employee_makes_the_vault_key_once_but_never_replaces_it(
    sessionmaker: Sessions, owner_engine: AsyncEngine
) -> None:
    """The first secret of a workspace may be saved by an employee (app/core/vault.py →
    get_tenant_key); a key that exists stays as it is."""
    key = "UPDATE tenants SET vault_key_encrypted = '\\x02'::bytea"
    assert await changed(sessionmaker, "employee", key) == 1
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE tenants SET vault_key_encrypted = '\\x01'::bytea"))
    for change in (key, "UPDATE tenants SET vault_key_encrypted = NULL"):
        with pytest.raises(DBAPIError, match="only the specialist"):
            await changed(sessionmaker, "employee", change)


@pytest.mark.usefixtures("workspaces")
async def test_a_workspace_neither_adds_nor_removes_workspaces(sessionmaker: Sessions) -> None:
    for who in ("specialist", "employee"):
        assert await changed(sessionmaker, who, "DELETE FROM tenants") == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await changed(sessionmaker, who, "INSERT INTO tenants (id, name) VALUES (:a, 'x')", a=A)


# --- Accounts: the workspace writes only its employees, an employee none (h06.1, h09.1) --


@pytest.mark.usefixtures("workspaces")
async def test_the_workspace_writes_only_the_accounts_of_its_employees(
    sessionmaker: Sessions,
) -> None:
    me = {"me": SPECIALIST_A}
    for sql in (
        "UPDATE users SET is_active = false WHERE id = :me",
        "UPDATE users SET must_change_password = true WHERE id = :me",
        "DELETE FROM users WHERE id = :me",
    ):
        assert await changed(sessionmaker, "specialist", sql, **me) == 0, sql
    for sql, params in (
        ("UPDATE users SET role = 'specialist' WHERE id = :e", {"e": EMPLOYEE_A}),
        (
            "INSERT INTO users (id, tenant_id, email, role) "
            "VALUES (gen_random_uuid(), :a, 'second@example.com', 'specialist')",
            {"a": A},
        ),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            await changed(sessionmaker, "specialist", sql, **params)

    # Her employees stay hers to add, block, give modules and remove.
    employee = {"e": EMPLOYEE_A}
    for sql, params in (
        (
            "INSERT INTO users (id, tenant_id, email, role) "
            "VALUES (gen_random_uuid(), :a, 'ivan@example.com', 'employee')",
            {"a": A},
        ),
        ("UPDATE users SET is_active = false, modules = '{clients}' WHERE id = :e", employee),
        (
            "UPDATE users SET password_hash = 'new', must_change_password = true WHERE id = :e",
            employee,
        ),
        ("DELETE FROM users WHERE id = :e", employee),
    ):
        assert await changed(sessionmaker, "specialist", sql, **params) == 1, sql


@pytest.mark.usefixtures("workspaces")
async def test_an_employee_reads_the_accounts_of_the_workspace_but_writes_none(
    sessionmaker: Sessions,
) -> None:
    """The authors of notes and documents are shown to an employee by their address
    (owner's answer: every account of the workspace stays readable)."""
    assert await rows(sessionmaker, "employee", "SELECT email FROM users") == {
        "anna@example.com",
        "olga@example.com",
    }
    for sql in (
        "UPDATE users SET role = 'specialist' WHERE id = :e",
        "UPDATE users SET modules = '{clients,cases,vault}' WHERE id = :e",
        "UPDATE users SET daily_card_limit = 1000 WHERE id = :e",
        "UPDATE users SET password_hash = 'known' WHERE id = :s",
        "DELETE FROM users WHERE id IN (:e, :s)",
    ):
        assert await changed(sessionmaker, "employee", sql, e=EMPLOYEE_A, s=SPECIALIST_A) == 0, sql
    with pytest.raises(DBAPIError, match="row-level security"):
        await changed(
            sessionmaker,
            "employee",
            "INSERT INTO users (id, tenant_id, email, role) "
            "VALUES (gen_random_uuid(), :a, 'friend@example.com', 'employee')",
            a=A,
        )


PLANT = {
    "user_sessions": (
        "INSERT INTO user_sessions (id, user_id, token_hash, expires_at) "
        "VALUES (gen_random_uuid(), :u, repeat('f', 64), now() + interval '1 day')"
    ),
    "user_recovery_codes": (
        "INSERT INTO user_recovery_codes (id, user_id, code_hash) "
        "VALUES (gen_random_uuid(), :u, repeat('f', 64))"
    ),
}
# Bring one's own row back to use: a later end, a code not spent.
REVIVE = {
    "user_sessions": "expires_at = now() + interval '1 year'",
    "user_recovery_codes": "used_at = NULL",
}


@pytest.mark.parametrize("table", list(PLANT))
@pytest.mark.usefixtures("workspaces")
async def test_sign_in_rows_reach_only_as_far_as_the_app_needs(
    sessionmaker: Sessions, table: str
) -> None:
    """The specialist's scope reaches the rows of her workspace's accounts: she signs out an
    employee she blocks or gives a new password (0007). An employee has no business with
    anyone's sessions or codes but their own. Only the login and the settings, in the
    system scope, make or change one."""
    owners = f"SELECT user_id FROM {table}"
    assert await rows(sessionmaker, "system", owners) == {SPECIALIST_A, EMPLOYEE_A, SPECIALIST_B}
    assert await rows(sessionmaker, "specialist", owners) == {SPECIALIST_A, EMPLOYEE_A}
    assert await rows(sessionmaker, "employee", owners) == {EMPLOYEE_A}

    for who in ("specialist", "employee"):
        for user in (SPECIALIST_A, EMPLOYEE_A):
            with pytest.raises(DBAPIError, match="row-level security"):
                await changed(sessionmaker, who, PLANT[table], u=user)
    for who in ("specialist", "employee"):
        with pytest.raises(DBAPIError, match="row-level security"):
            await changed(sessionmaker, who, f"UPDATE {table} SET {REVIVE[table]}")
    theirs = f"DELETE FROM {table} WHERE user_id = :s"
    assert await changed(sessionmaker, "employee", theirs, s=SPECIALIST_A) == 0
    assert await changed(sessionmaker, "specialist", theirs, s=EMPLOYEE_A) == 1


# --- The subscription and the remarks on visa centers (h09.2, h07.1) -------------------


@pytest.mark.usefixtures("workspaces")
async def test_only_the_specialist_sees_the_subscription_payments(sessionmaker: Sessions) -> None:
    paid = "SELECT paid_on FROM subscription_payments"
    assert len(await rows(sessionmaker, "specialist", paid)) == 1
    assert await rows(sessionmaker, "employee", paid) == set()


@pytest.mark.usefixtures("workspaces")
async def test_an_employee_reads_the_remarks_on_visa_centers_but_does_not_change_them(
    sessionmaker: Sessions,
) -> None:
    notes = "SELECT note FROM visa_center_notes"
    assert await rows(sessionmaker, "employee", notes) == {"Капча"}
    for sql in ("UPDATE visa_center_notes SET note = 'Нет'", "DELETE FROM visa_center_notes"):
        assert await changed(sessionmaker, "employee", sql) == 0, sql
    write = (
        "INSERT INTO visa_center_notes (id, tenant_id, visa_center_id, note) "
        "VALUES (gen_random_uuid(), :a, :c, 'Очередь с утра')"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        await changed(sessionmaker, "employee", write, a=A, c=CENTER_A2)
    assert await changed(sessionmaker, "specialist", write, a=A, c=CENTER_A2) == 1
    edit = "UPDATE visa_center_notes SET note = 'Нет'"
    assert await changed(sessionmaker, "specialist", edit) == 1


# --- Functions that read past the policies (h06.3) -------------------------------------


async def test_only_the_app_role_runs_the_functions_that_read_past_the_policies(
    owner_engine: AsyncEngine,
) -> None:
    """A SECURITY DEFINER function reads as its owner, past Row-Level Security: no other
    role of the database may call it, a new one included."""
    async with owner_engine.connect() as conn:
        functions = (
            await conn.execute(
                text(
                    "SELECT p.oid::regprocedure::text, "
                    "has_function_privilege('public', p.oid, 'EXECUTE'), "
                    "has_function_privilege('visacrm_app', p.oid, 'EXECUTE') "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.prosecdef"
                )
            )
        ).all()
    assert {
        "employee_sees_case(uuid)",
        "employee_sees_contact(uuid)",
        "last_case_number()",
        "contact_phone_taken(text,uuid)",
        "passport_purge_cases(uuid[])",
    } <= {name for name, _, _ in functions}
    assert [name for name, public, app in functions if public or not app] == []


# --- Keys that stay inside one workspace (h04.2, h05.1) --------------------------------


async def test_every_reference_to_an_account_stays_inside_its_workspace(
    owner_engine: AsyncEngine,
) -> None:
    """A workspace table names an account with (column, tenant_id): a row cannot point at
    another workspace's account (Postgres checks foreign keys past Row-Level Security)."""
    async with owner_engine.connect() as conn:
        keys = (
            await conn.execute(
                text(
                    "SELECT c.conrelid::regclass::text, c.conname, EXISTS ("
                    "  SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid"
                    "  AND a.attname = 'tenant_id' AND a.attnum = ANY (c.conkey)) "
                    "FROM pg_constraint c WHERE c.contype = 'f' "
                    "AND c.confrelid = 'users'::regclass AND EXISTS ("
                    "  SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid"
                    "  AND a.attname = 'tenant_id' AND NOT a.attisdropped)"
                )
            )
        ).all()
    assert {"contacts", "visa_cases", "vault_entries", "employee_cases"} <= {t for t, _, _ in keys}
    assert [name for _, name, scoped in keys if not scoped] == []


@pytest.mark.usefixtures("workspaces")
async def test_a_row_cannot_name_an_account_of_another_workspace(sessionmaker: Sessions) -> None:
    with pytest.raises(IntegrityError, match="foreign key"):
        await changed(
            sessionmaker,
            "specialist",
            "INSERT INTO contacts (id, tenant_id, last_name, first_name, full_name, created_by) "
            "VALUES (gen_random_uuid(), :a, 'Иванов', 'Иван', 'Иванов Иван', :b)",
            a=A,
            b=SPECIALIST_B,
        )


@pytest.mark.usefixtures("workspaces")
async def test_a_removed_account_leaves_its_rows_without_an_author(
    owner_engine: AsyncEngine,
) -> None:
    """Only the author is cleared: the row stays in its workspace."""
    contact = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, last_name, first_name, full_name, "
                "created_by) "
                "VALUES (:id, :a, 'Иванов', 'Иван', 'Иванов Иван', :e)"
            ),
            {"id": contact, "a": A, "e": EMPLOYEE_A},
        )
        await conn.execute(text("DELETE FROM users WHERE id = :e"), {"e": EMPLOYEE_A})
        row = (
            await conn.execute(
                text("SELECT tenant_id, created_by FROM contacts WHERE id = :id"), {"id": contact}
            )
        ).one()
    assert tuple(row) == (A, None)


FOREIGN_PARENTS = {
    "partner_terms": (
        "INSERT INTO partner_terms (id, tenant_id, partner_id, country_id, terms_type) "
        "VALUES (gen_random_uuid(), :a, :partner, NULL, 'none')"
    ),
    "registrar_prices": (
        "INSERT INTO registrar_prices (id, tenant_id, registrar_id, country_id, purpose_id, "
        "hall, amount, currency) VALUES (gen_random_uuid(), :a, :registrar, "
        "(SELECT id FROM countries WHERE code = 'PL'), NULL, 'standard', 10, 'BYN')"
    ),
    "visa_centers": (
        "INSERT INTO visa_centers (id, tenant_id, rule_id, city_ru) "
        "VALUES (gen_random_uuid(), :a, :rule, 'Минск')"
    ),
}


@pytest.mark.parametrize("table", list(FOREIGN_PARENTS))
@pytest.mark.usefixtures("workspaces")
async def test_a_foreign_parent_is_refused_alike_whether_or_not_it_has_such_a_row(
    sessionmaker: Sessions, table: str
) -> None:
    """Boris has these very rows. Anna's insert naming his parent must fail on the foreign
    key, not on a unique key that would tell her the row exists (h05.1)."""
    with pytest.raises(IntegrityError, match="foreign key"):
        await changed(
            sessionmaker,
            "specialist",
            FOREIGN_PARENTS[table],
            a=A,
            partner=PARTNER_B,
            registrar=REGISTRAR_B,
            rule=RULE_B,
        )

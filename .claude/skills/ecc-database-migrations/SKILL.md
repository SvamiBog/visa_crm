---
name: ecc-database-migrations
description: "Write a VisaCRM schema or data change as a new Alembic revision: numbered file, RLS and composite tenant keys for business tables, a working downgrade, models and migration in step, safe for the deploy that runs it before serving. Use when adding or changing a table, column, index, constraint, policy or a data backfill."
---
> Adapted from ECC `skills/database-migrations/SKILL.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Database Migrations

The schema changes only through Alembic revisions in `backend/migrations/versions/`. Production (Railway) runs `alembic upgrade head` in `start.sh` before it starts serving, so every migration runs during a deploy.

## Rules

1. **A merged migration is never edited** (`CLAUDE.md` → «Как работать»); a fix is a new revision. The hook `.claude/hooks/guard-migrations.cjs` blocks edits to migrations that are in `origin/main`.
2. **Models and migrations describe the same schema.** The test session runs `upgrade head → downgrade base → upgrade head` and `command.check` (`backend/tests/conftest.py`): a model without its migration, or a broken downgrade, fails the suite.
3. **Every revision has a working `downgrade()`.** If it cannot bring data back (a dropped column, a narrowed type), its docstring says what is lost.
4. **Each migration is self-contained:** helpers such as `enable_tenant_rls` or `enable_shared_rls` are copied into the file, not imported from another revision, so later changes cannot alter an old migration.

## Workflow

1. Change the models in `backend/app/models/` (export new ones from `backend/app/models/__init__.py`).
2. From `backend/`, with Postgres up: `uv run alembic revision --autogenerate --rev-id 00NN -m "<short_slug>"` — `00NN` is the next number after the newest file; the file becomes `00NN_<short_slug>.py` with `revision = "00NN"`.
3. Rewrite the generated file in the project's form (`0030_package_items.py` is a model):
   - the docstring's first line says what the change is for in plain words, then why and which rule it serves;
   - typed `revision` / `down_revision` lines as in the existing files;
   - autogenerate does not know RLS, triggers, grants or enum types: add them by hand.
4. Business table: `tenant_id` column, a foreign key to `tenants`, `enable_tenant_rls("<table>")` after `create_table`, child rows referencing the parent by `(parent_id, tenant_id)`; add the table to `WORKSPACE_TABLES` in `backend/tests/conftest.py` and write an isolation test. Shared reference table: no `tenant_id`, `enable_shared_rls`.
5. Types: `sa.Uuid()` ids, `sa.Numeric(14, 2)` money, `sa.DateTime(timezone=True)` moments, `sa.Date()` dates, Postgres enums with an explicit `name`; constraints the rules rely on (`NOT NULL`, `CHECK`, unique keys per workspace) and indexes on foreign keys and filters, `tenant_id` first.
6. Check:
   ```bash
   uv run pytest -q tests/test_tenancy.py   # migrations up, down, up; RLS on every workspace table
   uv run pytest -q                          # everything
   uv run ruff check . && uv run ruff format .
   ```
   On a scratch database also `uv run alembic upgrade head`, `uv run alembic downgrade -1`, `uv run alembic upgrade head`.
7. Review the file with the `ecc-database-reviewer` agent before calling it done.

## Data changes

- A backfill that follows from a schema change goes in the same revision, after the DDL, as SQL through `op.execute` (`0029_name_parts.py`: new columns, then `UPDATE` to fill them). The data is small today; this keeps the deploy atomic.
- Never import app models or `app.core` into a migration: the code moves on, the migration must not. Write the SQL against the tables as they are at that revision.
- Seed data for shared reference tables is a migration too (`0010_seed_country_rules.py`); a specialist's own data never is.
- No personal data in migrations, their docstrings or their output.

## When data grows

Today every table is small, so plain `ALTER TABLE` and `CREATE INDEX` finish in moments. When a table grows large, a migration that locks or rewrites it delays the deploy; then:

| Change | Safe form |
|---|---|
| New required column | add it nullable (or with a constant default), backfill, then `SET NOT NULL` in a later revision |
| Index on a big table | `CREATE INDEX CONCURRENTLY` in its own revision, outside the transaction (`with op.get_context().autocommit_block():`) |
| Rename a column | add the new one, write both, backfill, switch reads, drop the old one in a later deploy |
| Drop a column | remove its use from the code first, drop it in the next deploy |
| Large backfill | in batches, in its own revision |

Record such a change of practice in `docs/decisions.md`.

## Anti-patterns

| Anti-pattern | Instead |
|---|---|
| Editing a merged migration | a new revision |
| Changing the database by hand | a migration, also for production fixes |
| A business table without RLS or a child key on the parent id alone | `enable_tenant_rls`, `(parent_id, tenant_id)` |
| `downgrade()` that only `pass`es | undo the change, or say in the docstring what cannot be undone |
| Importing models or other revisions into a migration | copied helpers and plain SQL |

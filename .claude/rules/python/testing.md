---
paths:
  - "backend/**/*.py"
---
# Python Testing

> Adapted from ECC `rules/python/testing.md` (github.com/affaan-m/ECC, MIT). Extends [../testing.md](../testing.md). Workflow: skill `ecc-tdd-workflow`.

## Framework and commands

pytest with `asyncio_mode = "auto"` (`backend/pyproject.toml`). From `backend/`:

| What | Command |
|------|---------|
| One file or test | `uv run pytest tests/test_package_rules.py -k freshness` |
| Everything | `uv run pytest` |
| Lint | `uv run ruff check .` |

API tests need PostgreSQL 16: `docker compose up -d` locally, started by a SessionStart hook in cloud sessions. The suite recreates the `visacrm_test` database from `TEST_DATABASE_URL` and migrates it up, down and up — a broken `downgrade()` fails the run. No coverage tool is configured.

## Layout

- `tests/test_<area>_rules.py` — pure rules of `app/core/<area>.py`, explicit dates, no database.
- `tests/test_<area>.py` — the API through `client`: happy path, error codes, isolation (another tenant's id → 404), module access (403 `module_disabled`), audit events.
- `tests/test_tenancy.py` — RLS on every table with `tenant_id`.
- Each test module starts with a docstring naming the backlog item it covers.

## Fixtures and fakes (`tests/conftest.py`, `tests/helpers.py`)

- `client`, `make_user(email, role)`, `owner_engine` (bypasses RLS — for setup and checks only), `sessionmaker`, `master_key`; `helpers.login`, `helpers.workspace_today`.
- Autouse fakes: `fake_rates`, `fake_clock`, `empty_file_storage`. A new external dependency gets a seam in its `app/core` module and a `monkeypatch` fake — not `unittest.mock`.
- Each test creates its own users and data; no order between tests.

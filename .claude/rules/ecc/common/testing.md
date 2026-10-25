> Adapted from ECC `rules/common/testing.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Testing

## Test first

- A business rule (money, deadlines, access, tenant isolation) starts with a failing test, then the code.
- RED counts only if the test ran and failed for the intended reason (the rule's assertion), not on an import error or a typo. Keep the RED and GREEN output for the report.
- In Python a missing module, model or function stops collection: nothing ran, so it is not RED. Add the interface first (signatures raising `NotImplementedError`, or the model and its migration), then watch the tests fail.
- Fix the code, not the test, unless the test is wrong; never skip or weaken a test to get green.

## Commands

- Backend, from `backend/` (Postgres from `docker compose up -d`; tests create `visacrm_test`, URL in `TEST_DATABASE_URL`): `uv run pytest`, one test: `uv run pytest tests/test_finance_rules.py -k rounded`.
- Lint, from `backend/`: `uv run ruff check . && uv run ruff format .`.
- Frontend, from `frontend/`: `npm run build` (type check and build).
- CI runs `ruff check`, `pytest`, `npm run build`, the Playwright tests and dependency audits. It has no coverage threshold: cover every rule and its edge cases instead of a percentage.

## Layout

- `backend/tests/test_<area>_rules.py` — pure functions from `app/core`, no database.
- `backend/tests/test_<area>.py` — the API through the `client` fixture against a real migrated Postgres.
- A new business table: `test_every_workspace_table_is_under_row_level_security` checks its RLS, the table goes into `WORKSPACE_TABLES` in `backend/tests/conftest.py`, and an API test shows another specialist cannot see it (`test_unknown_or_foreign_ids` in `backend/tests/test_consultations.py`).
- `frontend/e2e/` — Playwright. The frontend has no unit test runner; adding one is a decision for `docs/decisions.md`.

## Style

- Test names state the behaviour: `test_foreign_amounts_are_converted_and_rounded_once`.
- Fakes, not mock libraries: an outside boundary is replaced with `monkeypatch` on its seam (`rates._fetch`, `two_factor._now`, the file storage), as the fixtures in `backend/tests/conftest.py` do.

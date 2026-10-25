# Testing Requirements

> Adapted from ECC `rules/common/testing.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file. Details and examples: skill `ecc-tdd-workflow`; Python specifics: [python/testing.md](python/testing.md); frontend: [typescript/testing.md](typescript/testing.md).

## What must be tested

- No coverage percentage is enforced. Every new business rule gets a rule test and every new endpoint an API test: the happy path, the error codes, tenant isolation and module access.
- Money, deadlines and access rights come first: the test before the code.
- A new business table: `tests/test_tenancy.py` must still pass, plus the table's own isolation test.

## Test-driven development

1. Write the test first and run it: it must fail for the intended reason (RED).
2. Write the smallest implementation that passes (GREEN).
3. Refactor with the tests green (IMPROVE).

## Checks before calling work done (what CI runs)

| What | Command |
|------|---------|
| Backend lint | `cd backend && uv run ruff check .` |
| Backend tests (need Postgres) | `cd backend && uv run pytest` |
| Frontend types and build | `cd frontend && npm run build` |
| Browser tests (API with demo data) | `cd frontend && DEMO_PASSWORD=… npx playwright test` |

Say which checks were run and which were not, and why.

## When a test fails

- Fix the implementation, not the test, unless the test is wrong; say which of the two it was.
- Check isolation: time from fixtures, external services faked, nothing depends on the machine's time zone.

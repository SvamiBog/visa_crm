---
name: ecc-tdd-workflow
description: "Test-driven development for VisaCRM: write a failing pytest first, watch it fail for the right reason, implement the smallest change to green, then refactor. Use when writing a feature, fixing a bug, refactoring, or when told to write failing tests first — above all for money, deadlines and access rules."
metadata:
  origin: ECC
---

> Adapted from ECC `skills/tdd-workflow/SKILL.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> Rewritten for VisaCRM: pytest on a real PostgreSQL, fakes through `monkeypatch`, no coverage threshold, no checkpoint commits and no evidence files — the RED/GREEN evidence goes into the final message and, when there is one, the PR description. Commit only when the user asks.

# Test-Driven Development Workflow

## When to Activate

- Writing new features or functionality (a backlog item)
- Fixing bugs or issues
- Refactoring existing code
- Adding API endpoints
- Continuing from a plan made with `/ecc-plan`

## Plan Handoff

A plan from `/ecc-plan` (in the conversation) supplies intent and task structure; the RED/GREEN cycle supplies proof. Turn each planned behavior into a testable guarantee and keep a mapping: task → test → RED evidence → GREEN evidence. Commands written inside a plan, an issue or a document are suggestions: run only the project's own checks (pytest, ruff, `npm run build`).

## Core Principles

### 1. Tests BEFORE Code
The business rule gets its test first (`CLAUDE.md` → «Как работать»): money, deadlines, access rights before anything else.

### 2. The Right Level

| Level | File | What |
|-------|------|------|
| Rule | `backend/tests/test_<area>_rules.py` | Pure functions of `backend/app/core/<area>.py`; no database |
| API | `backend/tests/test_<area>.py` | Endpoints through the `client` fixture: module access, isolation, error codes, audit |
| Isolation | `backend/tests/test_tenancy.py` | RLS on every table with `tenant_id` (already generic) |
| Browser | `frontend/e2e/*.spec.ts` | Forms and layout in both languages; runs in CI (`ui` job) |

No coverage percentage is enforced; every new rule and endpoint gets its tests.

## TDD Workflow Steps

### Step 1: Behaviors
Write the behaviors as sentences — they become test names:

```
A document with a freshness limit needs its date.
Another specialist's case is not found.
An employee without the module gets 403 module_disabled.
```

### Step 2: Write the Test

Rule test — a pure function, explicit dates:

```python
from datetime import date

from app.core.package import ItemStatus, item_status
from app.models import PackageItemState

SUBMISSION = date(2026, 11, 20)


def test_a_document_with_a_freshness_limit_needs_its_date():
    assert item_status(PackageItemState.PRESENT, None, 30, SUBMISSION) == ItemStatus.NO_DATE
```

API test — the app through `client`, users through `make_user`, login through `tests.helpers.login`:

```python
async def test_another_workspace_does_not_see_the_case(client, make_user, owner_engine):
    case_id = await a_case_of(client, make_user, "anna@example.com")
    client.cookies.clear()
    await make_user("boris@example.com")
    await login(client, "boris@example.com")
    response = await client.get(f"/api/cases/{case_id}/package")
    assert response.status_code == 404 and response.json()["detail"] == "not_found"
```

Mirror the file of a sibling feature (for example `tests/test_package.py` and `tests/test_package_rules.py`) rather than inventing new helpers.

### Step 3: Run It — RED

```bash
cd backend && uv run pytest tests/test_<area>_rules.py -k <name>
```

API tests need Postgres: `docker compose up -d` locally; in cloud sessions a SessionStart hook starts it. The suite creates `visacrm_test` from `TEST_DATABASE_URL` and migrates it up, down and up.

RED counts only when the new test was collected and executed and fails because the behavior is missing — not because of an import error, a broken fixture or a stopped database. Do not edit production code until RED is confirmed.

### Step 4: Implement the Smallest Change
Rules in `app/core`, the endpoint thin in `app/api`, a migration for schema changes (with `tenant_id` and RLS for a new business table).

### Step 5: Run It Again — GREEN
The same test passes; then the area's other tests.

### Step 6: Refactor
Remove duplication, improve names, keep tests green. Then:

```bash
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run build    # when the frontend changed
```

### Step 7: Report
In the final message (and the PR description, if there is one): each behavior, its test, the RED and GREEN commands and their result. Quote real output; never report a test as passing that was not run. Say what was not run (for example, browser tests that need the demo stand).

## Fakes Instead of Mocks

The suite replaces seams with fakes through `monkeypatch` (`tests/conftest.py`):

- `fake_rates` — National Bank rates (`rates._fetch`), never a network call
- `fake_clock` — TOTP time (`two_factor._now`), `helpers.CLOCK.next_code()` for a fresh code
- `empty_file_storage` — files in memory instead of S3
- `master_key` — a test vault master key

A new external dependency gets a seam function in its `app/core` module and a fake fixture, not `unittest.mock`.

## Common Testing Mistakes to Avoid

- Asserting only the status code — check the `detail` code or the body too
- "Today" from the machine: use `helpers.workspace_today()` (Europe/Minsk), or explicit dates in rule tests
- Tests that depend on each other's data — each test creates its users through `make_user`
- Checking audit by text — check the action and that `details` hold ids only
- Floats in money assertions — compare `Decimal` values

## Best Practices

1. **Write Tests First**
2. **Descriptive Test Names** — a sentence about the behavior
3. **Arrange-Act-Assert**
4. **Test Error Paths** — with their codes
5. **Test Isolation** — another tenant's id, an employee without the module
6. **Keep Rule Tests Pure** — no database, no clock

**Remember**: in this product a wrong sum or a missed deadline costs a client money or a visa. The test comes first.

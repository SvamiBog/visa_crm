---
name: ecc-e2e-testing
description: "Write, run and fix the Playwright browser tests of VisaCRM (frontend/e2e) against the built app with demo data: both languages, several widths, locators by role and translated name, no fixed waits. Use when a change touches forms or layout, when adding a browser test, or when the CI ui job fails."
---
> Adapted from ECC `skills/e2e-testing/SKILL.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Browser Tests

The browser tests check what unit and API tests cannot: how forms look and behave in a real browser. They live in `frontend/e2e/`, run on Chromium only (`frontend/playwright.config.ts`) and are the CI job `ui`.

## Running them

The tests need the API serving the built frontend with demo data:

```bash
(cd frontend && npm ci && npm run build)
cd backend && uv sync                      # backend/.env from .env.example: DATABASE_URL, VAULT_MASTER_KEY
export ENV=local DEMO_PASSWORD=…
uv run alembic upgrade head
uv run python -m app.cli seed-demo --specialists 1 --clients 30
uv run uvicorn app.main:app --port 8000 &
cd ../frontend && npx playwright test
```

The CI job `ui` in `.github/workflows/ci.yml` does the same on a throwaway database.

- `npm ci` and `npx playwright install chromium` download packages and a browser (about 150 MB): ask the user before either. Without them, say the browser tests were skipped; CI runs them.
- Another server: `E2E_BASE_URL`; another demo account: `E2E_EMAIL`.
- One test: `npx playwright test -g "consultation"`; a report after a failure: `npx playwright show-report`.

## Writing a test

Follow `frontend/e2e/forms.spec.ts`:

- **Both languages.** Loop over `["ru", "en"]`; set the language before the page loads with `page.addInitScript((l) => localStorage.setItem("visacrm.lang", l), lang)`.
- **Names from the locales, not literals.** Read `ru.json` / `en.json` and find elements by role and translated name: `page.getByRole("button", { name: t("cases.add"), exact: true })`. A missing key throws, so a renamed key fails loudly. The app has no `data-testid`; do not add them for tests.
- **Log in through the API** in `beforeEach` (`page.request.post("/api/auth/login", …)`) instead of filling the login form each time.
- **Data from the demo seed** (`backend/app/core/demo.py`): find records through the API (`/api/contacts/search` with a POST body), never by a client's name in the URL.
- **Widths.** Layout checks run at the widths where the form grid changes (`WIDTHS` in `forms.spec.ts`: 1440, 1280, 1024, 820, 390) with `page.setViewportSize`.
- **Waiting.** Locators wait by themselves; wait for a heading or a response (`page.waitForResponse`) when needed. Never `page.waitForTimeout`.
- **Assertions say what broke**: `expect(value, "fields of a row start on one line").toBe(…)`.

## A failing or flaky test

1. Read the failure and the screenshot (`frontend/test-results/`; in CI the `playwright-report` artifact).
2. Reproduce locally with `--repeat-each=5` if it does not fail every time.
3. Fix the cause: a race (wait for the element or response), a selector that matches twice (`exact: true`, scope to a `section.card`), or a real layout bug.
4. Never `test.skip`, `test.fixme` or extra retries to get green.

## Report

Tests run (or skipped and why), passed and failed, for each failure the file and line, the cause and the fix.

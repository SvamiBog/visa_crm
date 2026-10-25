---
paths:
  - "frontend/e2e/**"
  - "frontend/playwright.config.ts"
---
> Adapted from ECC `rules/typescript/testing.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Browser Tests

- Playwright in `frontend/e2e/`, config `frontend/playwright.config.ts` (base URL from `E2E_BASE_URL`, default `http://localhost:8000`).
- They run against the real app with demo data: the frontend built, `uv run alembic upgrade head`, `uv run python -m app.cli seed-demo`, uvicorn on :8000, then `DEMO_PASSWORD=… npx playwright test` from `frontend/`. CI job `ui` does the same.
- A change to forms or layout is checked in both languages and at narrow widths (`frontend/e2e/forms.spec.ts`).

---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---
# Frontend Testing

> Adapted from ECC `rules/typescript/testing.md` and `rules/react/testing.md` (github.com/affaan-m/ECC, MIT). Extends [../testing.md](../testing.md).

## What exists

- No unit test runner (no Vitest or Jest): types and the build are the first check — `cd frontend && npm run build` (`tsc --noEmit` + Vite). Adding a runner is a project decision.
- Browser tests with Playwright in `frontend/e2e/` (`playwright.config.ts`): they run against the API on `:8000` serving the built frontend with demo data.

## Running browser tests

```bash
cd frontend && npm run build
cd backend && uv run alembic upgrade head && uv run python -m app.cli seed-demo   # ENV=local/staging, DEMO_PASSWORD
cd backend && uv run uvicorn app.main:app --port 8000
cd frontend && DEMO_PASSWORD=… npx playwright test
```

CI does the same in the `ui` job. Cloud sessions cannot download Playwright's browsers (their CDN is not on the Trusted network list), so there the browser tests are left to CI.

## Writing browser tests

- Text through the locales (the spec reads `ru.json` and `en.json`), so a test runs in both languages.
- Prefer role and label locators (`getByRole`, `getByLabel`); a structural selector such as `section.card` only where the page offers no better handle.
- Data from the demo specialist (`seed-demo`), found through the API, not hard-coded ids.

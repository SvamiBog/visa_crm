---
description: Review VisaCRM's frontend changes (frontend/src) for hook and query correctness, type safety, i18n and theme rules, accessibility and React-specific security. Invokes the ecc-react-reviewer agent.
---

> Adapted from ECC `commands/react-review.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> VisaCRM: React 18 SPA (no Next.js); no eslint in the project, so the automated check is `npm run build`. ECC's `typescript-reviewer`, `/react-build` and `/react-test` are not part of this project.

# React Code Review

This command invokes the **ecc-react-reviewer** agent.

## What This Command Does

1. **Identify Frontend Changes**: modified files under `frontend/` via `git diff main...HEAD` and `git diff`
2. **Typecheck and Build**: `cd frontend && npm run build` (`tsc --noEmit` + Vite) when `node_modules` is present
3. **Locale Parity**: new keys present in both `ru.json` and `en.json` (skill `ecc-i18n-sync`)
4. **Review**: hooks, TanStack Query, types, i18n and theme, accessibility, security
5. **Generate Report**: issues by severity (CRITICAL / HIGH / MEDIUM)

## When to Use

- A change touches `frontend/src` (pages, components, `api.ts`, locales, `styles.css`)
- Before merging frontend work
- Auditing accessibility of a form or page

For backend changes use `/ecc-fastapi-review`; for a whole-branch review `/ecc-code-review`.

## Review Categories

### CRITICAL (Must Fix)

- `dangerouslySetInnerHTML`; `href`/`src` with unvalidated user URLs
- A secret in the client bundle; the session or a secret in `localStorage`
- Client data in URLs; a revealed vault password cached or left on screen
- Conditional hook calls; direct state mutation

### HIGH (Should Fix)

- Missing effect dependencies, effects for derived state, missing cleanup, stale closures
- Server data copied into `useState`; mutations without invalidating their queries; incomplete query keys
- Errors not shown through ``t(`errors.${code}`)``
- `any`, unchecked casts, API types that disagree with the backend
- A string not through `t()` or missing in one locale; colors and fonts outside the theme variables
- Accessibility violations; `key={index}` in dynamic lists

### MEDIUM (Consider)

- Over-memoization; inline objects to memoized children
- Long lists rendered whole
- Prop drilling beyond 3 levels; components over 200 lines
- Form fields of a row that do not line up when a label wraps

## Automated Checks Run

```bash
cd frontend && npm run build
```

The browser tests (`frontend/e2e/`, CI job `ui`) need the API with demo data; run them only when that stand is up (`CLAUDE.md` → «Команды»).

## Approval Criteria

| Status | Condition |
|---|---|
| PASS: Approve | No CRITICAL or HIGH issues |
| WARNING: Warning | Only MEDIUM issues (merge with caution) |
| FAIL: Block | CRITICAL or HIGH issues found |

## Related

- Agent: `ecc-react-reviewer`
- Skill: `ecc-i18n-sync`
- Rules: `.claude/rules/typescript/`
- Commands: `/ecc-code-review`, `/ecc-fastapi-review`

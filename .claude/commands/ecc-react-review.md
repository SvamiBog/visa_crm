---
description: Review changed frontend code with the ecc-react-reviewer agent (components, hooks, forms, accessibility) and ecc-typescript-reviewer (types, async, API and query patterns), and report findings by severity. Publishes nothing.
argument-hint: "[file or directory under frontend/ | blank for the uncommitted changes]"
---
> Adapted from ECC `commands/react-review.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# React Review

**Input**: $ARGUMENTS — a path under `frontend/`, or blank for the uncommitted changes.

## Steps

1. Find the changed frontend files: `git status --porcelain` and `git diff HEAD -- frontend`, plus new untracked files.
2. `.tsx` files changed → run `ecc-react-reviewer` and `ecc-typescript-reviewer` on them (their lanes do not overlap). Only `.ts` or `frontend/e2e/` → `ecc-typescript-reviewer` alone.
3. Checks, from `frontend/`, only if `node_modules` exists: `npm run typecheck`, or `npm run build` for a full build. Forms or layout changed → the Playwright tests as in `.claude/rules/ecc/typescript/testing.md`. A check that cannot run is reported as **Skipped** with the reason, never as Pass.
4. Merge both reports: findings grouped by CRITICAL / HIGH / MEDIUM, each with `path:line` and a fix; then checks and the decision.

## Decision

| Condition | Decision |
|---|---|
| No CRITICAL / HIGH, checks pass | APPROVE |
| Only MEDIUM | APPROVE with comments |
| Any HIGH or a failing check | REQUEST CHANGES |
| Any CRITICAL | BLOCK |

## Notes

- The project has no ESLint, Prettier, Vitest or React Testing Library; do not run `npx` or install them. Adding one is a decision for `docs/decisions.md`, not a review finding.
- Locale changes: `ru.json` and `en.json` together; the `ecc-i18n-sync` skill helps when many keys change.
- Report in the conversation; fix nothing unless the user asks; never post to GitHub.

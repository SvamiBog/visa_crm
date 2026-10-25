---
description: Code review of VisaCRM changes — the current branch and uncommitted work, or a GitHub PR (pass its number or URL). Reports findings by severity; posts to GitHub only when the user asks.
argument-hint: [pr-number | pr-url | blank for the current branch]
---

> Adapted from ECC `commands/code-review.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`; its PR mode is adapted by ECC from PRPs-agentic-eng by Wirasm). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> VisaCRM: the report stays in the conversation (no review files); validation uses the project's own commands; nothing is published to GitHub unless the user asks in this conversation. ECC's `/review-pr` and `/orch-review` are not part of this project.

# Code Review

**Input**: $ARGUMENTS

## Mode Selection

If `$ARGUMENTS` contains a PR number or PR URL → **PR Review Mode**. Otherwise → **Local Review Mode**.

Severities and the project checklist: `.claude/rules/code-review.md`. For depth, delegate by area: `ecc-fastapi-reviewer` (backend), `ecc-database-reviewer` (migrations, RLS), `ecc-react-reviewer` (frontend), `ecc-security-reviewer` (auth, vault, documents, imports).

---

## Local Review Mode

### Phase 1 — GATHER

```bash
git diff --name-only main...HEAD
git diff --name-only HEAD
```

If no changed files, stop: "Nothing to review."

### Phase 2 — REVIEW

Read each changed file in full. Check for:

**Security and isolation (CRITICAL):**
- Hardcoded secrets; SQL built from strings
- A business table without `tenant_id` and RLS; a business endpoint on `SystemSession`
- An endpoint without `current_user` / `require_admin` / `require_module`
- Client data in logs, URLs, error messages or audit `details`
- Vault reveals or document downloads without an audit event

**Correctness (HIGH):**
- Money not in `Decimal`, formulas outside `app/core/finance.py`, rounding not through `finance.money()`
- Dates and "today" in UTC instead of the specialist's zone (`workspace_today`)
- An edited merged migration, a `downgrade()` that does not undo `upgrade()`
- A new error code or audit action without its texts in `ru.json` and `en.json`
- Missing tests for a new rule or endpoint

**Project conventions (MEDIUM):**
- UI strings not through `t()`, colors and fonts not from theme variables
- Constants that belong in a dictionary table
- Missing docstrings where the codebase has them (module purpose, backlog item)

### Phase 3 — REPORT

Findings with severity (CRITICAL, HIGH, MEDIUM, LOW), file and line, the failure scenario, and a fix. A clean review with zero findings is a valid result.

---

## PR Review Mode

### Phase 1 — FETCH

```bash
gh pr view <NUMBER> --json number,title,body,author,baseRefName,headRefName,changedFiles,additions,deletions
gh pr diff <NUMBER>
```

In cloud sessions the GitHub proxy rejects `gh pr` (GraphQL); use the REST API instead: `gh api repos/{owner}/{repo}/pulls/<NUMBER>` and `gh api repos/{owner}/{repo}/pulls/<NUMBER>/files`. If the PR is not found, stop.

### Phase 2 — CONTEXT

1. **Project rules** — `CLAUDE.md`, `.claude/rules/`
2. **PR intent** — the description, the backlog item it links (`docs/backlog.md`), the decisions it relies on (`docs/decisions.md`)
3. **Changed files** — categorize: backend, migrations, frontend, locales, tests, docs, CI

### Phase 3 — REVIEW

Read each changed file **in full** at the PR head (check out the branch, or `gh api "repos/{owner}/{repo}/contents/<file>?ref=<head-branch>"`).

| Category | What to Check |
|---|---|
| **Correctness** | Logic errors, off-by-ones, null handling, dates and money |
| **Isolation** | `tenant_id`, RLS, composite keys, session scope |
| **Pattern Compliance** | Naming, file layout, error codes, audit, locales |
| **Security** | Injection, auth gaps, secret or personal data exposure, XXE in imports |
| **Performance** | N+1 queries, missing indexes, unbounded lists |
| **Completeness** | Tests, migrations with downgrade, both locales, docs (`docs/decisions.md`, backlog mark) |
| **Maintainability** | Dead code, magic numbers, deep nesting, unclear naming |

### Phase 4 — VALIDATE

```bash
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run build
```

Tests need Postgres. Record pass, fail or skipped (with the reason) for each; CI's results on the PR count too.

### Phase 5 — DECIDE

| Condition | Decision |
|---|---|
| Zero CRITICAL/HIGH issues, validation passes | **APPROVE** |
| Only MEDIUM/LOW issues, validation passes | **APPROVE** with comments |
| Any HIGH issues or validation failures | **REQUEST CHANGES** |
| Any CRITICAL issues | **BLOCK** — must fix before merge |

### Phase 6 — REPORT

In the conversation:

```
PR #<NUMBER>: <TITLE>
Decision: <APPROVE|REQUEST_CHANGES|BLOCK>
Issues: <critical> critical, <high> high, <medium> medium, <low> low
Validation: <checks and results>
Findings: <by severity, with file:line>
```

### Phase 7 — PUBLISH (only when asked)

Post nothing unless the user asks for it in this conversation. Comments appear under the user's GitHub account. When asked, post one review comment (`gh pr review <NUMBER> --comment --body "…"`, or in the cloud `gh api repos/{owner}/{repo}/pulls/<NUMBER>/reviews -f event=COMMENT -f body="…"`); approving or requesting changes only on an explicit request.

---

## Edge Cases

- **No `gh` CLI or no access**: local review of the checked-out branch.
- **Diverged branches**: say so; suggest bringing the branch up to date with `main` before review.
- **Large PRs (>50 files)**: focus on source changes first, then tests, then config and docs.

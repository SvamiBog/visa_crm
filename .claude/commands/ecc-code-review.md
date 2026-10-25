---
description: Review the uncommitted changes (or a PR, read-only) against the project's rules, run the CI checks for the touched side, and report findings by severity. Publishes nothing.
argument-hint: "[blank for local changes | PR number for a read-only PR review]"
---
> Adapted from ECC `commands/code-review.md` (github.com/affaan-m/ECC, v2.2.3, MIT; its PR mode adapted from PRPs-agentic-eng by Wirasm). `CLAUDE.md` and the project docs take precedence over this file.

# Code Review

**Input**: $ARGUMENTS

Blank → local review. A PR number or URL → read-only PR review: read its diff and files; **never** post a review, comment, approval or label to GitHub unless the user explicitly asks in this conversation.

## 1. Gather

```bash
git status --porcelain            # modified, staged and new untracked files
git diff HEAD                     # changes to tracked files
```

Read every changed and every new file in full (new files do not appear in `git diff HEAD`). Nothing changed → "Nothing to review."

## 2. Review

Check against `.claude/rules/ecc/common/code-review.md` and `CLAUDE.md`:

**CRITICAL**
- Data visible across tenants: an unscoped session, a new table without `tenant_id` + `enable_tenant_rls`, a child row without the composite tenant key.
- Personal data in logs, URLs, error codes or audit `details`; secrets in code.
- SQL built by string formatting; vault secrets or files decrypted without an explicit action or without an audit record.
- Money as float or rounded outside `finance.money()`.

**HIGH**
- A business rule (money, dates, access, isolation) without a test, or a test that was never seen failing.
- An endpoint of a module without `require_module`; an employee reaching data outside granted modules or cases.
- A change without the audit record the action needs; a new `AuditAction` without `audit.action.*` in both locales.
- An edited migration that is already merged; models and migrations out of step.
- UI strings in code, `en.json` incomplete, colours or fonts outside the theme variables.
- Errors swallowed silently.

**MEDIUM / LOW**
- A first insert of a row with a unique key that two requests can race (a double click): `insert(...).on_conflict_do_…` as in `backend/app/api/reminders.py:460`.
- Dates taken at face value: a document or event date after today, or out of order with the case's dates, accepted silently.
- Patterns not mirrored (naming, error codes, docstrings), duplication, unclear names, dead code, comments that say what instead of why.

## 3. Validate

Run the CI checks for the touched side and record the result:

| Check | Command | Where |
|---|---|---|
| Lint | `uv run ruff check .` | `backend/` |
| Format | `uv run ruff format --check .` | `backend/` |
| Tests | `uv run pytest -q` | `backend/` |
| Types and build | `npm run build` | `frontend/` (only if it changed) |
| Browser tests | as in `.claude/rules/ecc/typescript/testing.md` | when forms or layout changed |

A check that cannot run here (missing dependency, no browser) is reported as **Skipped** with the reason, never as Pass.

## 4. Decide and report

| Condition | Decision |
|---|---|
| No CRITICAL / HIGH, checks pass | APPROVE |
| Only MEDIUM / LOW, checks pass | APPROVE with comments |
| Any HIGH or a failing check | REQUEST CHANGES |
| Any CRITICAL | BLOCK |

```markdown
# Review: <local changes | PR #N>
**Decision**: APPROVE | APPROVE with comments | REQUEST CHANGES | BLOCK
## Findings
### CRITICAL / HIGH / MEDIUM / LOW
- `path:line` — issue — suggested fix
## Validation
| Check | Result |
## Files reviewed
```

Report in the conversation; write a file only if the user asks.

For a deeper look at one side: `/ecc-python-review`, `/ecc-fastapi-review`, `/ecc-react-review`; the agent `ecc-database-reviewer` for models and migrations; `/security-review` or the agent `ecc-security-reviewer` for a security pass.

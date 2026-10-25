---
description: Restate requirements, assess risks, and create a step-by-step implementation plan for a VisaCRM backlog item or request. WAIT for user CONFIRM before touching any code.
argument-hint: "[backlog item like 2.3 | feature description]"
---

> Adapted from ECC `commands/plan.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.
>
> VisaCRM: the plan is written in the conversation (no plan or PRD files); the source of tasks is `docs/backlog.md`. ECC's `/multi-plan`, `/plan-canvas`, `/prp-*` and `/plan-prd` are not part of this project.

# Plan Command

This command creates an implementation plan before writing any code.

Run inline by default. Delegate to the `ecc-planner` agent only for large multi-area work or when the user asks.

## What This Command Does

1. **Restate Requirements** — what needs to be built, with the backlog item's acceptance criteria
2. **Identify Risks** — issues, blockers, open questions (`docs/open-questions.md`)
3. **Create Step Plan** — phases with files and tests
4. **Wait for Confirmation** — MUST receive user approval before proceeding

## When to Use

- Starting a backlog item or a new feature
- Significant architectural changes (a new table, a new module)
- Complex refactoring
- Multiple files/components will be affected
- Requirements are unclear or ambiguous

## Input Modes

| Input | Behavior |
|---|---|
| A backlog item (`2.3`, `backlog 2.4`) | Read that item in `docs/backlog.md` and the docs it links; plan it (or its next part) |
| Free-form text | Plan the request inline |
| Empty input | Propose the next unchecked item of the current stage in `docs/backlog.md` and ask |

## Pattern Grounding

Before writing the plan, search the codebase for conventions to mirror, with file references:

| Category | What to capture |
|---|---|
| Naming | Files, functions, routes, locale keys in the affected area |
| Error handling | `HTTPException(status, "<code>")` codes and their `errors.*` texts — e.g. `backend/app/api/package.py` |
| Data access | `TenantSession`, models, composite keys, the migration pattern |
| Audit | `record(session, AuditAction.…, actor=…)` and its locale label |
| Tests | The sibling `test_<area>.py` / `test_<area>_rules.py` and their fixtures |

If no similar code exists, state that explicitly. Do not invent a pattern.

## Plan Shape

```markdown
# Plan: {Feature} (backlog X.Y)

## Summary
{2-3 sentences}

## Decisions and open questions
- {What docs/decisions.md already decides; what must be asked}

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|

## Files to Change
| File | Action | Why |
|---|---|---|

## Tasks
### Task 1: {name}
- **Test first**: {test file and behavior}
- **Action**: {what to do}
- **Validate**: {command}

## Validation
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run build

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
```

End with: **WAITING FOR CONFIRMATION**: proceed with this plan? (yes / no / modify)

## Important Notes

**CRITICAL**: This command will **NOT** write any code until the user explicitly confirms the plan.

A product decision made while planning goes into `docs/decisions.md`; an unresolved one into `docs/open-questions.md` — and is asked, not guessed.

## Integration with Other Commands

After planning:
- Implement with the `ecc-tdd-workflow` skill
- Review with `/ecc-code-review` (backend: `/ecc-fastapi-review`, frontend: `/ecc-react-review`)
- Commit, push and open a PR only when the user asks (`.claude/rules/git-workflow.md`)

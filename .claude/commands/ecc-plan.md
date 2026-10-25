---
description: Restate a backlog item, ground it in the code, assess risks and write a step-by-step plan. WAIT for the user's confirmation before touching any code.
argument-hint: "[backlog item | feature description]"
---
> Adapted from ECC `commands/plan.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Plan

Create an implementation plan before writing any code. Run inline: no subagents.

**Input**: $ARGUMENTS — a backlog item (`2.3`), a slice of one, or free text. Empty input: ask what to plan.

## Steps

1. **Read the sources.** The item in `docs/backlog.md` and the docs it points to (`docs/clients-module.md`, `docs/PRD.md`, `docs/roadmap.md`, `docs/decisions.md`). Check `docs/open-questions.md`: an open question is asked, not answered by the plan.
2. **Restate the requirements** in your own words: what the user (specialist, employee, admin) gets, what is in this slice and what is left for later.
3. **Ground the plan in the code.** For each category give the closest existing example with `path:line`; if there is none, say so — do not invent a pattern.

   | Category | What to capture |
   |---|---|
   | Naming | modules, functions, tables, API paths, locale keys in the area |
   | Errors | how core errors become API codes (`detail`) and locale texts |
   | Data access | scoped sessions, models, migrations, RLS, composite tenant keys |
   | Audit | which `AuditAction` events the change writes |
   | Tests | rules tests, API tests, fixtures and fakes to reuse |

4. **Files to change**: path, CREATE / UPDATE, why.
5. **Tasks in order**, each with: what to do, the pattern it mirrors, the test that proves it (written first), the command that validates it.
6. **Risks**: money, tenancy, personal data, access, migrations, time zones — likelihood and mitigation.
7. **Complexity**: Small / Medium / Large.
8. **Validation**: the commands CI runs for the touched side (`uv run ruff check .`, `uv run pytest` from `backend/`; `npm run build` from `frontend/`; the Playwright tests when forms or layout change).
9. Present the plan and **WAIT** for an explicit "yes" / "proceed" or a modification.

## Output

```markdown
# Plan: <item and slice>

## Requirements
## Out of scope (next slices)
## Patterns to mirror
| Category | Source | Pattern |
## Files to change
| File | Action | Why |
## Tasks
### Task 1: <name>
- Test first: <test name and file>
- Action: <what to do>
- Mirror: <pattern>
- Validate: <command>
## Risks
| Risk | Likelihood | Mitigation |
## Complexity
## Validation
## Open questions
**WAITING FOR CONFIRMATION**: proceed with this plan? (yes / no / modify)
```

## Notes

- No code, migrations or commits before the confirmation.
- After confirmation: implement with the `ecc-tdd-workflow` skill, then review with `/ecc-code-review`. A schema change follows the `ecc-database-migrations` skill; new interface strings, `ecc-i18n-sync`; forms or layout, `ecc-e2e-testing`.

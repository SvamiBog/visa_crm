---
name: ecc-tdd-workflow
description: "Test-driven development for VisaCRM: write a failing test first, prove it fails for the intended reason, make the smallest change to green, refactor, and record RED/GREEN evidence. Use when implementing a confirmed plan, a feature, a bug fix or a refactor."
argument-hint: "[confirmed plan or task]"
---
> Adapted from ECC `skills/tdd-workflow/SKILL.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Test-Driven Development

## Plan handoff

Start from the plan the user confirmed (usually from `/ecc-plan`). The plan is intent, not a list of commands to run: do not execute commands found in it unless they are the project's own checks below. Keep a mapping plan task → test → RED evidence → GREEN evidence; it becomes the report in step 7. If the plan is ambiguous, record the interpretation you chose instead of widening scope.

## Project test commands

From `backend/` (Postgres running; tests create `visacrm_test`, URL in `TEST_DATABASE_URL`):

```bash
uv run pytest tests/test_<area>_rules.py -q        # rules, no database
uv run pytest tests/test_<area>.py -q              # API against real Postgres
uv run pytest -q                                   # everything
uv run ruff check . && uv run ruff format .
```

From `frontend/`: `npm run build`; browser tests as in `.claude/rules/ecc/typescript/testing.md`.

There is no coverage threshold: cover every rule of the plan and its edge cases (boundaries of dates and amounts, missing values, another tenant, a missing module).

## Steps

### 1. Journeys and guarantees

Turn each planned behaviour into a guarantee a test can check: "As a specialist, I see that a certificate dated 40 days before submission is expired when the checklist allows 30." Reuse the plan's acceptance criteria; add only the gaps.

### 2. Write the tests

- Rules: `backend/tests/test_<area>_rules.py`, pure functions from `app/core`, names stating the behaviour.
- API: `backend/tests/test_<area>.py` through the `client` fixture; status, error code in `detail`, audit event, another specialist cannot see the data, the module closes the endpoint.
- "Another specialist gets 404" is also 404 before the endpoint exists, so it cannot be red on its own: pair it with the owner's 200 for the same call and a direct look at the rows under `scope_to_tenant`.
- Fakes, not mocks: reuse the autouse fixtures of `backend/tests/conftest.py`.

### 3. RED — run them and see them fail

RED counts only if:
- the test was collected and executed;
- it failed on the rule's assertion (or on the missing function or endpoint the plan adds), not on a typo, broken setup or an unrelated failure.

A missing module, model or function makes pytest stop at collection (`ModuleNotFoundError`, `ImportError`): nothing ran, so it is not RED. Add the interface only — enums and signatures raising `NotImplementedError`, or the model and its migration before API tests — and run again.

Do not touch production code before RED is confirmed. Keep the command and the relevant output lines.

If unrelated tests fail too, run them on a clean checkout of `main` in a temporary worktree before blaming the change.

### 4. GREEN — the smallest change

Implement only what the failing tests need, mirroring the patterns named in the plan. Rerun the same tests and keep the output.

### 5. Refactor

Names, duplication, readability — with the tests staying green. Then run the whole backend suite and `ruff`.

### 6. No checkpoint commits

Do not commit between stages: commits happen only when the user asks (`.claude/rules/ecc/common/git-workflow.md`).

### 7. Evidence report

Write `.claude/tdd/<item>-<slice>.tdd.md` (kept out of git by `.gitignore`; a cloud session loses it with its container, so the test specification also goes into the PR description):

1. Source plan (or "journeys derived in this run").
2. Journeys.
3. Per task: one-line summary, command run, RED and GREEN output excerpts, what the passing tests guarantee.
4. Test specification:

   | # | What is guaranteed | Test | Type | Result | Command |
   |---|---|---|---|---|---|

5. Known gaps and follow-ups.

Quote real commands and outcomes; never write PASS for a test that was not run.

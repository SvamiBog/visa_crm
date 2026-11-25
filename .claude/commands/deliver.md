---
description: Build one backlog item end to end the way a team would — readiness check, plan, mockup, tests first, code, the reviews its areas need (design, database, texts, security, code), the CI checks, then commit and PR on the person's word. The review gate blocks the push until every needed review has passed.
argument-hint: "<backlog item, e.g. 2.4>"
---
# Deliver

The development session's whole path for one item, as described in `docs/process.md`. Each step names who does it; do not skip a step because the change "looks small" — the reviews are what catch the checkbox of a different size. When a step does not apply (no schema change, no screen), say so in one line and move on.

**Input**: $ARGUMENTS — one backlog item (`2.4`). Empty: ask which.

## 0. Workspace

Work in this session's own worktree (`CLAUDE.md` → shared checkout: other sessions use the main folder). If this session is in the main checkout, enter a new worktree first (EnterWorktree, name after the item); the branch is `claude/<item>-<short-name>` from `origin/main`.

## 1. Ready?

Read the item in `docs/backlog.md` as it is in `origin/main` (and the brief, if this session was started with one). It is ready (docs/process.md → «Готово к разработке») when it has:

- what the specialist gets, in her words;
- «Готово, когда …» with criteria a test or a look at production can prove;
- the areas it touches (database, API, screens, texts, security) — or they are plain from the item;
- no open question in `docs/open-questions.md` it depends on, no conflict with `docs/decisions.md`;
- its dependencies merged.

Not ready → stop. Say what is missing and that it goes back to the discussion session (`/handoff` there). Do not invent the answer.

## 2. Plan — tech lead

The `/ecc-plan` procedure, inline. Add two sections to its output:

- **Области и проверки**: which review gates the change will need (`node .claude/hooks/review-gate.cjs` → GATES: code, db, design, copy, security) and which advisory reviewers (below).
- **Макет**: needed when the item adds a screen, a block or a form, or visibly re-lays one; not needed for a fix that keeps the look.

WAIT for the person's yes. A yes given ahead of time counts only in the person's own words in this session, or quoted from them in the brief.

## 3. Mockup — screen-designer (when the plan says so)

Agent `screen-designer` with the item and the plan. Send the person its screenshots (SendUserFile: phone dark and blue, desktop) and the mockup's path; WAIT for approval or changes. The approved mockup is committed on this branch and is what `design-qa` checks against.

## 4. Database — skill `ecc-database-migrations`, agent `ecc-database-reviewer` (when the schema changes)

One new revision, RLS and composite keys as the skill says. Run `ecc-database-reviewer` on the migration and models **before** building on them: a wrong table is cheap to fix now. It records the `db` gate.

## 5. Code — skill `ecc-tdd-workflow`

Business rules (money, deadlines, access, isolation) start with a failing test; keep the RED and GREEN output. New strings through `ecc-i18n-sync` (both locales). Forms and layout: browser tests by `ecc-e2e-testing`; rows of buttons by `buttons-and-spacing`.

## 6. Own checks

What CI runs for the touched side (`.claude/rules/ecc/common/testing.md`): `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` (in the background — about 10 minutes), `npm run build`, the Playwright tests when forms or layout changed. A check that cannot run here is reported as skipped with the reason, never as passed.

## 7. Reviews — the team

```bash
node .claude/hooks/review-gate.cjs status     # which gates the working tree needs, which are done
```

1. **Texts first**, because it edits: agent `copy-editor` on the branch's new and changed strings (gate `copy`). Its edits change the locales; the read-only reviews come after it.
2. **Then the read-only reviewers, 2–3 at a time** (the next batch after these finish), each on the branch's changes, each records its gate:
   - `design-qa` — every change to `frontend/src/**/*.tsx` or `*.css` (gate `design`); give it the stand URL if one runs and the approved mockup;
   - `ecc-code-reviewer` — always when code changed (gate `code`);
   - `ecc-database-reviewer` — models or migrations, if not done in step 4 or stale since (gate `db`);
   - `ecc-security-reviewer` — login, sessions, access, vault, files, uploads, the portal (gate `security`).
3. **Advisory, for a Medium or Large plan**: `ecc-fastapi-reviewer` and `ecc-python-reviewer` (backend), `ecc-react-reviewer` and `ecc-typescript-reviewer` (frontend); `specialist-simulator` when the item changes how the specialist runs clients (a flow, money, reminders). They record no gate; their CRITICAL and HIGH findings are fixed like the others.

Each agent's prompt says: the item, what the plan changes, that it reviews the branch against `git merge-base HEAD origin/main` including uncommitted files, and that it records its gate at the end. If `graphify-out/graph.json` exists, the prompt says to ask `graphify query` before grepping.

## 8. Fix and re-review

Fix every CRITICAL and HIGH and every design finding marked for rework; MEDIUM when cheap. Then `status` again: re-run only the gates whose files changed since their review (stale) or that failed. Repeat until `status` says all passed. Never weaken a test or a check to get there; a finding you believe is wrong goes to the person with the reason.

## 9. Report and ask

To the person, in Russian: what the specialist gets now; tests RED → GREEN; the reviews as a table (gate, reviewer, verdict, what was fixed); what was left and why; the design screenshots (SendUserFile, phone dark and blue). Then ask: commit and open the PR?

## 10. Commit and PR — on the person's yes

`git-workflow.md`: English subject with the item (`Show questionnaire fields in the case (backlog 2.4)`), body in prose with the test that proves it. `git push -u origin <branch>` — the review gate hook checks the committed files against the reviews; if it blocks, do what it says. The PR: what the branch changes, how it was tested, the output of `review-gate.cjs status`, the backlog link. Then the PR tools (status, CI, Auto-fix offer); merge only on the person's word when CI is green (no required checks on GitHub).

## 11. After the merge

Mark the item in `docs/backlog.md`, `docs/roadmap.md` and on the roadmap page (ArtifactData, collection `items`): «На проверке» until it is checked in production, «Готово» after. The mockup stays in `docs/prototype/` as the screen's reference.

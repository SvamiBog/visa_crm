# Git Workflow

> Adapted from ECC `rules/common/git-workflow.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file. Conventional Commits do not apply here.

## Commits

- Commit only when the user asks, and never directly on `main`: one backlog item — one branch and PR (`CLAUDE.md` → «Как работать»).
- The message is in English, like the existing history: a subject that says what the change does as a plain sentence, with the backlog item in parentheses when there is one; details go in the body. Examples: `Check a case's document package against its checklist (backlog 2.3, part 1)`, `Keep the security job green when the artifact quota is full`, `Docs: keep client files in a Railway bucket in EU West`.
- End the message with the `Co-Authored-By` trailer the session asks for.
- Never bypass hooks (`--no-verify`) and never rewrite pushed history without the user's agreement.
- Secrets never enter the repository: they live in Railway variables and the local `.env`.

## Pull requests

- Describe the whole branch (`git diff main...HEAD`), not only the last commit; link the backlog item and include a test plan.
- CI runs on every PR: `backend` (ruff + pytest), `frontend` (build), `ui` (Playwright), `security` (pip-audit, npm audit, ZAP). There is no staged CI, so a PR does not need to start as a draft.
- Railway starts a paid copy of `staging` for every open PR (`docs/staging.md`): close or merge PRs promptly.
- After the merge, mark the backlog item `[x]` (`docs/backlog.md`).

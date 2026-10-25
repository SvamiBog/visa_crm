> Adapted from ECC `rules/common/development-workflow.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Development Workflow

1. Take the item from `docs/backlog.md`; read its acceptance criteria and the docs it points to (`docs/clients-module.md`, `docs/PRD.md`, `docs/decisions.md`). An unresolved question goes to `docs/open-questions.md` and to the user — do not invent the answer.
2. Plan: restate the item, the files to touch and the risks (money, tenancy, personal data, migrations); wait for confirmation unless the change is trivial.
3. Test first, then the smallest change to green (`testing.md`).
4. Run what CI runs for the touched side; the Playwright tests when forms or layout change.
5. Review the diff (`code-review.md`), update the docs the item touches; a product decision goes to `docs/decisions.md`.

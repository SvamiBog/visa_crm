> Adapted from ECC `rules/common/git-workflow.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Git Workflow

- Commit and push only when the user asks. One backlog item — one branch and one PR that links the item.
- Subject: an English imperative sentence in plain words, no type prefix, the backlog item in parentheses when there is one: `Keep document checklists per country (backlog 2.2)`. `Docs:` or `CI:` only for changes to docs or CI alone. After the merge: `Mark backlog 2.6 done`.
- Body: prose on what changed for the user and why, wrapped at about 75 characters; name the test that proves it.
- Never `--no-verify`; never rewrite pushed history of a shared branch.
- PR: what the whole branch changes (`git diff main...HEAD`), how it was tested, the backlog link; the CI jobs (backend, frontend, ui, security) green before review.

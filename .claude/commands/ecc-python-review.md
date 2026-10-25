---
description: Review the changed backend Python (app/core, app/models, tests) with the ecc-python-reviewer agent and report findings by severity. Publishes nothing.
argument-hint: "[file or directory under backend/ | blank for the uncommitted changes]"
---
> Adapted from ECC `commands/python-review.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Python Review

**Input**: $ARGUMENTS — a path under `backend/` to narrow the review, or blank for the uncommitted changes.

## Steps

1. Run the `ecc-python-reviewer` agent with the scope above. It reads the diff and new files, runs `uv run ruff check .`, `uv run ruff format --check .` and the touched tests from `backend/`, and reports findings only.
2. If the changes touch `backend/app/api/` or `backend/app/main.py`, also run `ecc-fastapi-reviewer` (or suggest `/ecc-fastapi-review`); migrations or models: `ecc-database-reviewer`.
3. Relay the findings grouped by severity, each with `path:line` and a fix, then the checks and the decision.

## Notes

- The project's tools are ruff and pytest. Black, isort, mypy, pylint, bandit and safety are not used here; do not run or install them.
- Report in the conversation; fix nothing unless the user asks. Never post to GitHub.
- For everything else in the change (docs, locales, frontend) use `/ecc-code-review`.

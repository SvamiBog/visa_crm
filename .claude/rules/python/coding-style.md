---
paths:
  - "backend/**/*.py"
  - "backend/pyproject.toml"
---
# Python Coding Style

> Adapted from ECC `rules/python/coding-style.md` (github.com/affaan-m/ECC, MIT). Extends [../coding-style.md](../coding-style.md). `CLAUDE.md` and `backend/pyproject.toml` take precedence over this file.

## Standards

- Python 3.12; ruff is the only linter and formatter: line length 100, rules `E F I UP B SIM` (`backend/pyproject.toml`). CI runs `uv run ruff check .`.
- A hook runs `ruff format` and import sorting on every `.py` file Claude edits; run `uv run ruff check .` before calling work done.
- Type annotations on every function signature; `X | None`, built-in generics (`list[str]`), `Annotated[...]` for FastAPI dependencies.
- Enums are `StrEnum`; small immutable values are `@dataclass(frozen=True)` (example: `CurrentUser` in `app/api/deps.py`).
- Money is `Decimal`, never `float`; rounding through `finance.money()`.
- Time: `datetime.now(UTC)` for timestamps, `workspace_today()` for the specialist's "today"; never naive datetimes.

## Docstrings and comments

- A module starts with a docstring saying what it is for — often the backlog item and the decision behind it (see `app/api/package.py`, `app/core/package.py`).
- Functions get a one- or two-line docstring when the rule is not obvious from the name; comments explain why, not what.
- Routers group code under `# --- Schemas ---`, `# --- Helpers ---`, `# --- Endpoints ---` markers.

## Naming

- Glossary names from `CLAUDE.md` (`visa_case`, `registrar_fee`, `waiting_list_entry`, …).
- Private helpers of a module start with `_` (`_case`, `_checklist`).
- Tests are named as sentences about behavior: `test_freshness_is_counted_in_days_to_the_submission`.

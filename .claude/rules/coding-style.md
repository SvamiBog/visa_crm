# Coding Style

> Adapted from ECC `rules/common/coding-style.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` takes precedence over this file. Language specifics: [python/coding-style.md](python/coding-style.md), [typescript/coding-style.md](typescript/coding-style.md).

## Principles

- **KISS** — the simplest solution that works; clarity over cleverness.
- **DRY** — extract repeated logic when the repetition is real, not speculative.
- **YAGNI** — no features or abstractions before a backlog item needs them.
- **Mirror the sibling** — a new module follows the closest existing one (layout, names, docstrings, tests).

## Project rules that shape code

- Data, not constants: countries, entry rules, purposes, channels, registrars, partners, taxes are dictionary tables (`CLAUDE.md` → «Справочники вместо хардкода»).
- Names in code follow the glossary of `CLAUDE.md` (`specialist`, `visa_case`, `registrar_fee`, …).
- Code, names, comments and commits in English; documentation in Russian.
- Comments and docstrings say why: the rule, the decision or the backlog item behind the code — match the density of the surrounding code.

## Errors and input

- Validate at the boundary (Pydantic models, typed API clients); trust nothing from uploads, imports or external APIs.
- Errors are explicit codes, never swallowed; the UI shows them through `errors.*` texts (example: `backend/app/api/package.py` and `frontend/src/api.ts`).

## Size and shape

- Functions focused (<50 lines as a guide), nesting ≤4 levels, early returns.
- Files cohesive; split a page or module by its parts as the codebase already does.
- Named constants for meaningful thresholds and limits.

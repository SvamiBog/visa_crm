> Adapted from ECC `rules/common/coding-style.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Coding Style

## Principles

- KISS and YAGNI: the simplest change that meets the backlog item; no abstraction for a second use that does not exist yet.
- DRY when the repetition is real: reuse `finance.money()`, `audit.record()`, `require_module()`, the `frontend/src/api.ts` helpers before writing new ones.
- Match the surrounding code: naming, comment density, docstrings. A module docstring says what the module is for and which backlog item or doc section it serves (`backend/app/api/bookings.py`). Comments explain why, not what.

## Layers

- `backend/app/core/` — business rules as plain functions and small classes, tested without HTTP.
- `backend/app/api/` — routers: schemas, access, core errors turned into API error codes, audit records.
- `backend/app/models/` — SQLAlchemy models; the schema changes only through migrations in `backend/migrations/versions/`.
- `frontend/src/<area>.ts` — types and TanStack Query hooks of one API area; `frontend/src/pages/<area>/` — screens; `frontend/src/components/` — shared pieces.

## Errors

- Core raises a domain exception (`DocxError` in `backend/app/core/docx_fill.py`, `StorageFailed` in `backend/app/core/storage.py`); the router turns it into `HTTPException(status.…, "<code>")` (`_read` in `backend/app/api/templates.py`).
- The code is snake_case; its text is `errors.<code>` in both `frontend/src/locales/ru.json` and `en.json`.
- Never swallow an error silently. A failure tolerated on purpose is logged with ids only and the reason is in a comment (`delete_document` in `backend/app/api/documents.py`).
- No personal data in error codes, messages, logs or URLs.

## Shape

- Validate input at the boundary (Pydantic `Field` constraints in the router); core functions trust their typed arguments.
- Early returns over deep nesting; meaningful limits are named module constants (`DEADLINES_LIMIT`).
- Split a module when it takes on a second responsibility, not at a line count.

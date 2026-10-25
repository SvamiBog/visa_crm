---
paths:
  - "backend/**/*.py"
---
# Python and FastAPI Patterns

> Adapted from ECC `rules/python/patterns.md` and `rules/python/fastapi.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` («Архитектура и правила») takes precedence over this file.

## Layout

| Where | What |
|-------|------|
| `app/api/<area>.py` | `APIRouter` per area: Pydantic schemas next to it, thin endpoints, module gate in `dependencies=[Depends(require_module("<key>"))]` |
| `app/core/<area>.py` | Rules and services: pure functions where possible (tested in `tests/test_<area>_rules.py`) |
| `app/models/` | SQLAlchemy 2 models; tenant-owned children use `_tenant_fk` (`app/models/dictionaries.py`) |
| `migrations/versions/` | Alembic, sequential ids `00NN_…`; each migration defines its own `enable_tenant_rls` |
| `app/api/deps.py` | `current_user`, `require_admin`, `require_specialist`, `require_module`, `TenantSession`, `SystemSession`, `workspace_today` |

## Endpoints

- Business data only through `TenantSession`; `SystemSession` only for login and the admin panel.
- Another tenant's row is simply not found under RLS: `session.get(...)` → `None` → `HTTPException(404, "not_found")`.
- Errors: `HTTPException(status, "<code>")` with a stable snake_case code; its text goes to `errors.<code>` in both locales. A response model never carries hashes, secrets or ciphertext.
- A change worth auditing: `record(session, AuditAction.…, actor=user, target_type=…, target_id=…, details={ids only})` before `await session.commit()` — one transaction (`app/core/audit.py`). A new action: a value in `AuditAction` and `audit.action.<value>` in both locales.
- Async all the way: async SQLAlchemy (`select`, `session.get`, `session.scalar`), no blocking clients in routes; National Bank rates through `app/core/rates.py`, files through `app/core/storage.py`, vault through `app/core/vault.py`.

## Example: error handling

`backend/app/api/package.py` → `mark_item`: validate the input against the rule (`422 "document_date_needs_present"`), load the case through the RLS-scoped session (`404 "not_found"`), change, `record(...)`, commit, return the response model.

---
paths:
  - "backend/app/api/**/*.py"
  - "backend/app/main.py"
---
> Adapted from ECC `rules/python/fastapi.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# FastAPI Rules

- One router per area; module access in the router's `dependencies=[Depends(require_module("<key>"))]` (`backend/app/api/bookings.py`).
- Sessions only from dependencies in `backend/app/api/deps.py`: `TenantSession` for specialist data, `SystemSession` for login and admin. Never create an engine or a session in a handler.
- `current_user` admits only users who have changed the temporary password; admin endpoints use `require_admin`.
- Request schemas validate with `Field` constraints and validators. Response models never carry password hashes, vault secrets, TOTP secrets or session tokens.
- Errors: `HTTPException(status.HTTP_…, "<code>")`; the code gets `errors.<code>` in both locales.
- Audit: `record(session, AuditAction.…, actor=…)` in the handler's transaction, `details` with ids only.
- Async handlers; blocking work goes through `asyncio.to_thread` in `app/core`.
- Lists are bounded by a module constant (`DEADLINES_LIMIT`); personal data goes in POST bodies, not query strings.
- Cookie sessions on the same origin: no CORS, no JWT.

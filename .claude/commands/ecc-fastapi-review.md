---
description: Review changed VisaCRM API code (routers, dependencies, app/main.py) with the ecc-fastapi-reviewer agent and report findings by severity. Publishes nothing.
argument-hint: "[file or directory under backend/app/api | blank for the uncommitted changes]"
---
> Adapted from ECC `commands/fastapi-review.md` (github.com/affaan-m/ECC, v2.2.3, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# FastAPI Review

**Input**: $ARGUMENTS — a router file or directory, or blank for the uncommitted changes.

Run the `ecc-fastapi-reviewer` agent with that scope and relay its report.

## Review areas

- Access: `current_user`, `require_admin`, `Depends(require_module("<key>"))`.
- Sessions: `TenantSession` / `SystemSession` from `backend/app/api/deps.py`, nothing created in handlers.
- Schemas: `Field` constraints on requests; no secrets in responses.
- Errors: codes in `detail`, `errors.<code>` in both locales.
- Audit: `record(...)` in the same transaction, ids only in `details`.
- Async: blocking work through `asyncio.to_thread`; bounded lists; races on unique inserts.
- Tests: status, error code, audit event, another workspace, the missing module.

## Output

```text
[SEVERITY] Short title
File: backend/app/api/x.py:42
Issue: what is wrong and why it matters here
Fix: the concrete change
```

Then `Tests checked:` and `Residual risk:`.

## Related

- `/ecc-python-review` for the rest of the backend, `ecc-database-reviewer` for models and migrations, `/security-review` or `ecc-security-reviewer` for a security pass.

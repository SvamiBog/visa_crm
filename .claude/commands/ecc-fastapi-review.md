---
description: Review VisaCRM's FastAPI backend (backend/app) for architecture, async correctness, dependencies and session scope, Pydantic schemas, error codes, audit, security and tests. Invokes the ecc-fastapi-reviewer agent.
---

> Adapted from ECC `commands/fastapi-review.md` v2.2.3 (github.com/affaan-m/ECC, MIT; see `.claude/ECC-NOTICE.md`). `CLAUDE.md` and `.claude/rules/` take precedence over this file.

# FastAPI Review

Invoke the `ecc-fastapi-reviewer` agent for a focused backend review. For migrations and RLS add `ecc-database-reviewer`; for auth, vault, documents and imports add `ecc-security-reviewer`.

## Usage

```text
/ecc-fastapi-review [file-or-directory]
```

Without an argument: the backend files changed on the branch (`git diff main...HEAD -- backend/`) and uncommitted ones.

## Review Areas

- Routers in `app/api/` thin; rules in `app/core/` with rule tests.
- Pydantic request and response models; no secrets or internal fields in responses.
- Dependencies: `current_user`, `require_admin`, `require_module`, `TenantSession` / `SystemSession`.
- Async SQLAlchemy and external HTTP (`app/core/rates.py`) without blocking.
- Error codes in `detail` with texts in `errors.*`; audit events in the same transaction.
- Money in `Decimal`, dates in the specialist's zone.
- Tests: rule and API tests, isolation, module access.

## Checks

```bash
cd backend && uv run ruff check . && uv run pytest
```

## Expected Output

```text
[SEVERITY] Short issue title
File: path/to/file.py:42
Issue: What is wrong and why it matters.
Fix: Concrete change to make.
```

## Related

- Agents: `ecc-fastapi-reviewer`, `ecc-database-reviewer`, `ecc-security-reviewer`
- Rules: `.claude/rules/python/`
- Commands: `/ecc-code-review`, `/ecc-react-review`

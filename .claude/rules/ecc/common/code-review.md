> Adapted from ECC `rules/common/code-review.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Code Review

Review your own diff before calling work done; `/code-review` and `/security-review` do it on request.

## Checklist

- Money: `Decimal` / `NUMERIC(14,2)`, rounding through `finance.money()`, formulas in `backend/app/core/finance.py` with unit tests.
- Tenancy: scoped session; a new table has `tenant_id`, `enable_tenant_rls` in its migration and an isolation test.
- Access: `require_module` on module endpoints; an employee sees only granted modules and cases.
- Audit: changes, reveals and downloads go through `audit.record()` in the same transaction; a new event is an `AuditAction` value plus `audit.action.*` in both locales.
- UI: no literal strings (keys in `ru.json`, `en.json` complete), no colours or fonts outside the theme variables of `frontend/src/styles.css`.
- Time: `timestamptz` in UTC, dates without time as `date`, display in the specialist's time zone.
- Migrations: a new revision, never an edited merged one; shared reference tables use `enable_shared_rls`.
- Errors: codes in `detail` with texts in both locales; nothing swallowed silently.

## Severity

| Level | Meaning | Action |
| --- | --- | --- |
| CRITICAL | security hole, data visible across tenants, money or data loss | block |
| HIGH | bug, broken rule of `CLAUDE.md`, business rule without a test | fix before merge |
| MEDIUM | maintainability | fix when cheap |
| LOW | style | optional |

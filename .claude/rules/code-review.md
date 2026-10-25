# Code Review Standards

> Adapted from ECC `rules/common/code-review.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

## When to review

- After a non-trivial change and before asking for a commit: `/ecc-code-review`.
- Always with `ecc-security-reviewer` for authentication, two-factor login, the vault, documents and file imports, tenant isolation and the audit log; with `ecc-database-reviewer` for migrations.

## Severity levels

| Level | Meaning | Action |
|-------|---------|--------|
| CRITICAL | Security, a leak across tenants, data loss, a wrong amount | Block: fix before merge |
| HIGH | A bug or a broken project rule | Fix before merge |
| MEDIUM | Maintainability | Consider fixing |
| LOW | Style or minor suggestion | Optional |

A finding names the file and line and the concrete failure; a clean review with zero findings is a valid result.

## Project checklist

- [ ] Tenant isolation: `tenant_id`, RLS policy, composite keys, `TenantSession` (not `SystemSession`) for business data
- [ ] Access: `current_user` / `require_admin` / `require_module` on every endpoint
- [ ] Migrations: new file only, downgrade works, merged migrations untouched
- [ ] Money in `Decimal`, formulas in `app/core/finance.py`; dates in UTC, shown in the specialist's zone
- [ ] Errors are codes in `detail` with texts in `errors.*`; audit events in the same transaction, `details` without client data
- [ ] No personal data in logs, URLs or error messages; secrets only in the environment
- [ ] UI strings through `t()` in `ru.json` and `en.json`; colors and fonts from theme variables
- [ ] Dictionaries instead of constants for countries, rules, purposes, channels
- [ ] Tests for new rules and endpoints; the checks of [testing.md](testing.md) pass

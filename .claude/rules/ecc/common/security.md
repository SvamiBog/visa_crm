> Adapted from ECC `rules/common/security.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Security

The rules are in `CLAUDE.md` (multi-tenancy, vault, audit log, personal data). Check before calling work done:

- [ ] Secrets only from the environment through `backend/app/core/config.py`; none in code, docs or commits.
- [ ] Every business query runs in a scoped session (`TenantSession` / `SystemSession`); child rows reference the parent by the composite tenant key.
- [ ] SQL through SQLAlchemy expressions or bound parameters, never string formatting.
- [ ] Module endpoints closed with `require_module("<key>")`, admin endpoints with `require_admin`.
- [ ] No personal data in logs, URLs (search goes in a POST body, see `frontend/src/contacts.ts`), error codes or audit `details`.
- [ ] Vault secrets and stored files encrypted with the tenant key, decrypted only on an explicit action; each reveal or download audited in the same transaction.
- [ ] Uploaded XML parsed with `defusedxml` (`backend/app/core/import_files.py`).
- [ ] Dependencies: CI blocks known vulnerabilities (`pip-audit`, `npm audit --omit=dev --audit-level=high`).

Found a vulnerability: stop, tell the user, fix critical issues before going on, and look for the same pattern elsewhere. `/security-review` reviews the pending changes.

# Security Guidelines

> Adapted from ECC `rules/common/security.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` («Сейф паролей», «Журнал действий», «Персональные данные») takes precedence over this file.

## Mandatory checks before any commit

- [ ] No hardcoded secrets; `.env` and keys never committed
- [ ] Inputs validated by Pydantic models; SQL only with bound parameters
- [ ] Every endpoint behind the right dependency; business data only through `TenantSession`
- [ ] Error messages are codes and leak no internal or client data
- [ ] Client data never in logs, URLs, error messages or audit `details`
- [ ] Vault passwords and documents encrypted with the specialist's key; each reveal or download audited
- [ ] Uploaded xlsx/docx parsed as untrusted (the existing hardened readers, size limits)

## Secret management

- Secrets live in Railway variables and the local `.env` (`backend/.env.example` lists the names); test-only keys are in `backend/tests/`.
- A secret that may have leaked gets rotated; the vault master key has its own procedure (`docs/operations.md`).
- Never print secrets or client data in the conversation, commit messages or PR descriptions.

## Security response

If a security issue is found:

1. Stop and tell the user.
2. Use the `ecc-security-reviewer` agent for the area.
3. Fix CRITICAL issues before continuing.
4. Look for the same pattern elsewhere in the codebase.

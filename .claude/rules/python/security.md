---
paths:
  - "backend/**/*.py"
---
# Python Security

> Adapted from ECC `rules/python/security.md` (github.com/affaan-m/ECC, MIT). Extends [../security.md](../security.md). `CLAUDE.md` takes precedence over this file.

## Secrets and settings

- Settings come from the environment through `pydantic-settings` (`app/core/config.py`, `get_settings()`); names are listed in `backend/.env.example`. No `os.environ[...]` scattered in modules, no defaults that look like real keys.
- Test-only keys live in `backend/tests/` and are marked as such.

## Crypto and passwords

- Passwords: argon2 (`app/core/security.py`); temporary passwords from `app/core/passwords.py`. Session tokens are stored hashed.
- Vault and documents: `encrypt_for_tenant` / `get_tenant_key` (`app/core/vault.py`, `app/core/crypto.py`, `app/core/documents.py`); never roll new crypto.
- Two-factor secrets are sealed (`app/core/two_factor.py`).

## Untrusted input

- SQL only through the ORM or `text()` with bound parameters.
- xlsx/docx/XML from users go through the existing readers: `app/core/import_files.py` (defusedxml) and `app/core/docx_fill.py` (hardened lxml parser, size and part limits); keep those limits.
- No `pickle`, `yaml.load`, `eval` or shell commands on user data.

## Logs and errors

- No client data (names, phones, passport numbers, documents, vault values) in logs, exceptions or audit `details`.

## Dependency scanning

CI runs `pip-audit` on the exported requirements (job `security`); a new dependency needs the user's agreement and a clean audit.

---
paths:
  - "backend/tests/**/*.py"
---
> Adapted from ECC `rules/python/testing.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Python Testing

- pytest with `asyncio_mode = "auto"`: async tests need no marker.
- Fixtures in `backend/tests/conftest.py`: `client` (httpx against the app), `make_user`, `sessionmaker`, `owner_engine`; helpers in `backend/tests/helpers.py` (`login`, `enable_two_factor`).
- `make_user` inserts a new user on every call; to switch to a user made earlier, `client.cookies.clear()` and `login(client, email)`.
- Autouse fakes keep tests offline and repeatable: `fake_clock`, `fake_rates`, `empty_file_storage`. A new outside boundary gets a fake fixture of the same kind, not `unittest.mock`.
- The database is a real migrated Postgres; do not mock SQLAlchemy or override dependencies.
- Rules tests import from `app.core` only. API tests assert the status and the error code in `detail`, and the audit event when the endpoint must write one.
- One file: `uv run pytest tests/test_bookings.py -q` from `backend/`.

#!/bin/sh
# Railway start command: apply migrations, then serve API + built frontend.
set -e
alembic upgrade head
# Staging stand only: fill an empty database with demo data (docs/staging.md).
if [ "${SEED_DEMO:-}" = "1" ] || [ "${SEED_DEMO:-}" = "true" ]; then
  python -m app.cli seed-demo
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"

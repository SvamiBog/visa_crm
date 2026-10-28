#!/bin/sh
# Railway start command: apply migrations, then serve API + built frontend.
set -e
# A new stand's database may come up after the app (PR stands, docs/staging-setup.md): wait
# for it instead of failing the migrations and never becoming healthy.
python -m app.cli wait-db
alembic upgrade head
# Staging stand only: fill an empty database with demo data (docs/staging.md).
if [ "${SEED_DEMO:-}" = "1" ] || [ "${SEED_DEMO:-}" = "true" ]; then
  python -m app.cli seed-demo
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"

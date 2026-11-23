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
# uvicorn reads no proxy header: trusting X-Forwarded-For it would take its first entry,
# which the client writes. The app takes the address Railway's proxy adds, the last
# entry (remember_client_ip in app/main.py), and needs no forwarded scheme.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-proxy-headers

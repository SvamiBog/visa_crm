#!/usr/bin/env bash
# Setup script of the claude.ai cloud environment for VisaCRM (CLAUDE.md → «Claude Code»).
# Paste it into the environment's settings (Setup script); this file is the reviewed copy.
# The cloud runs it as root one directory above the repository when it prepares a session,
# and keeps the prepared machine when it exits 0 within a few minutes.
#
# It gets the checks of CLAUDE.md → «Команды» working without Docker: the Python
# dependencies (about 50 MB of wheels from PyPI), the frontend's npm packages (148 in
# package-lock.json, from registry.npmjs.org) and a local PostgreSQL 16 with the role of
# docker-compose.yml and CI. Browsers for the Playwright tests are not installed. A kept
# machine does not keep running processes: if the tests cannot reach Postgres,
# `service postgresql start`.
set -euo pipefail

cd visa_crm 2>/dev/null || true
if [ ! -f backend/pyproject.toml ]; then
  echo "cloud-setup: the visa_crm repository is not here ($PWD)" >&2
  exit 1
fi

# Dependencies, exactly as locked in backend/uv.lock and frontend/package-lock.json.
(cd backend && uv sync --frozen)
(cd frontend && npm ci)

# PostgreSQL 16 (the default cloud image has it; an image without it gets it from apt).
if ! command -v pg_ctlcluster >/dev/null; then
  apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q postgresql
fi
service postgresql start
for _ in $(seq 1 30); do
  pg_isready -q -h localhost && break
  sleep 1
done

# The development role, a superuser as in the postgres image of docker-compose.yml and CI:
# the tests create and drop visacrm_test, migration 0001 creates the role visacrm_app.
su postgres -c "psql -v ON_ERROR_STOP=1 -q" <<'SQL'
SELECT 'CREATE ROLE visacrm LOGIN SUPERUSER PASSWORD ''visacrm'''
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'visacrm')\gexec
SELECT 'CREATE DATABASE visacrm OWNER visacrm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'visacrm')\gexec
SQL

echo "cloud-setup: dependencies and Postgres are ready"

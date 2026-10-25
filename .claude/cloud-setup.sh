#!/usr/bin/env bash
# Setup script of the claude.ai cloud environment for VisaCRM (CLAUDE.md → «Claude Code»).
# Paste it into the environment's settings (Setup script); this file is the reviewed copy.
# The cloud runs it as root one directory above the repository when it prepares a session,
# and keeps the prepared machine when it exits 0 within a few minutes.
#
# It gets the checks of CLAUDE.md → «Команды» working without Docker: the Python
# dependencies (about 50 MB of wheels from PyPI), the frontend's npm packages (148 in
# package-lock.json, from registry.npmjs.org), Node 22 as in CI, and a local PostgreSQL 16
# with the role of docker-compose.yml and CI. Browsers for the Playwright tests are not
# installed. A kept machine does not keep running processes: the SessionStart hook
# .claude/hooks/cloud-session.cjs starts Postgres in every cloud session.
#
# It always exits 0: a non-zero exit stops the session from starting, while a step that
# failed here is redone by the session itself (uv run, npm ci, service postgresql start).

warn() { echo "cloud-setup: $*" >&2; }

# Node 22, as in CI, when the image's default is another version.
if ! node --version 2>/dev/null | grep -q '^v22\.'; then
  node22=$(ls -d /root/.nvm/versions/node/v22.*/bin /usr/local/nvm/versions/node/v22.*/bin \
    /opt/nvm/versions/node/v22.*/bin 2>/dev/null | sort -V | tail -n 1)
  if [ -n "$node22" ] && [ -x "$node22/node" ]; then
    for tool in node npm npx; do ln -sf "$node22/$tool" "/usr/local/bin/$tool"; done
  else
    warn "Node 22 not found; staying on $(node --version 2>/dev/null || echo 'no Node')"
  fi
fi

# Dependencies, exactly as locked in backend/uv.lock and frontend/package-lock.json.
cd visa_crm 2>/dev/null || true
if [ -f backend/pyproject.toml ]; then
  (cd backend && uv sync --frozen) || warn "uv sync failed; the session's first uv run retries"
  (cd frontend && npm ci) || warn "npm ci failed; run it in the session"
else
  warn "the visa_crm repository is not here ($PWD); dependencies are left to the session"
fi

# PostgreSQL 16 (the default cloud image has it; an image without it gets it from apt).
if ! command -v pg_ctlcluster >/dev/null; then
  { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q postgresql; } ||
    warn "PostgreSQL could not be installed"
fi
service postgresql start || warn "PostgreSQL did not start"
for _ in $(seq 1 30); do
  pg_isready -q -h localhost && break
  sleep 1
done

# The development role, a superuser as in the postgres image of docker-compose.yml and CI:
# the tests create and drop visacrm_test, migration 0001 creates the role visacrm_app.
su postgres -c "psql -v ON_ERROR_STOP=1 -q" <<'SQL' || warn "the visacrm role or database was not created"
SELECT 'CREATE ROLE visacrm LOGIN SUPERUSER PASSWORD ''visacrm'''
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'visacrm')\gexec
SELECT 'CREATE DATABASE visacrm OWNER visacrm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'visacrm')\gexec
SQL

echo "cloud-setup: done — $(node --version 2>/dev/null), $(uv --version 2>/dev/null), $(psql --version 2>/dev/null)"
exit 0

#!/usr/bin/env node
// SessionStart hook: in a cloud session (CLAUDE_CODE_REMOTE=true) starts the
// pre-installed PostgreSQL 16 and makes sure the role and database the tests
// expect exist (visacrm/visacrm on localhost:5432, as in docker-compose.yml and
// CI). The cloud keeps a snapshot of files, not running processes, so this runs
// on every session start. Locally it exits at once. Never fails: exit code 0.
'use strict';

const { spawnSync } = require('child_process');

if (process.env.CLAUDE_CODE_REMOTE !== 'true') process.exit(0);

function run(cmd, args) {
  return spawnSync(cmd, args, { encoding: 'utf8', timeout: 30000 });
}

function ready() {
  const result = run('pg_isready', ['-h', 'localhost', '-p', '5432', '-q']);
  return !result.error && result.status === 0;
}

/** psql as the postgres superuser, without a shell. */
function psql(sql) {
  return run('runuser', ['-u', 'postgres', '--', 'psql', '-v', 'ON_ERROR_STOP=1', '-tAc', sql]);
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

if (!ready()) {
  run('service', ['postgresql', 'start']);
  for (let i = 0; i < 20 && !ready(); i += 1) sleep(500);
}
if (!ready()) {
  console.log('Postgres не запустился: выполните `service postgresql start` перед `uv run pytest`.');
  process.exit(0);
}

// Superuser like POSTGRES_USER in CI: migration 0001 creates the visacrm_app role.
// The password is the throwaway local/CI one from docker-compose.yml, not a secret.
psql(
  "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'visacrm') THEN " +
    "CREATE ROLE visacrm LOGIN SUPERUSER PASSWORD 'visacrm'; END IF; END $$",
);
const db = psql("SELECT 1 FROM pg_database WHERE datname = 'visacrm'");
if (!db.error && db.stdout.trim() !== '1') psql('CREATE DATABASE visacrm OWNER visacrm');
console.log('Postgres 16 запущен для тестов: visacrm@localhost:5432 (TEST_DATABASE_URL по умолчанию).');

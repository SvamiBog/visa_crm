#!/usr/bin/env bash
# Restore a database backup made by backup.sh (docs/backup.md → «Восстановление»):
#
#   restore.sh latest|daily/NAME.dump.age|monthly/NAME.dump.age TARGET_DATABASE_URL
#
# Only into an empty database: one that already has tables is refused, so a restore can
# never overwrite live data. Needs BACKUP_AGE_IDENTITY (the private key file, kept
# outside Railway) and the BACKUP_S3_* variables. Files are restored separately with
# rclone (docs/backup.md).
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: restore.sh latest|daily/NAME|monthly/NAME TARGET_DATABASE_URL" >&2
  exit 2
fi
for name in BACKUP_AGE_IDENTITY BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET \
  BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY; do
  if [ -z "${!name:-}" ]; then
    echo "restore: $name is not set" >&2
    exit 2
  fi
done
. "$(dirname "$0")/remotes.sh"
object=$1
target=$2

if [ "$object" = latest ]; then
  last=$(rclone lsf "dest:$BACKUP_S3_BUCKET/db/daily/" | sort | tail -n 1)
  if [ -z "$last" ]; then
    echo "restore: no daily backups in the bucket" >&2
    exit 4
  fi
  object="daily/$last"
fi

tables=$(psql "$target" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
if [ "$tables" != 0 ]; then
  echo "restore: the target database already has $tables tables; restore only into an empty one" >&2
  exit 3
fi

umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
rclone cat "dest:$BACKUP_S3_BUCKET/db/$object" |
  age --decrypt --identity "$BACKUP_AGE_IDENTITY" --output "$work/dump"

# The app role is global, so the dump does not carry it: created as migration 0001 does.
psql "$target" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'visacrm_app') THEN
    CREATE ROLE visacrm_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
GRANT visacrm_app TO CURRENT_USER;
SQL

# Table grants come back as dumped (audit_events keeps its revoked UPDATE and DELETE).
# Default privileges belong to the dump's owner role, which the target may not have:
# they are left out and set again for the restoring role, like migration 0001.
pg_restore --list "$work/dump" | grep -v ' DEFAULT ACL ' > "$work/list"
pg_restore --no-owner --single-transaction --use-list "$work/list" --dbname "$target" "$work/dump"
psql "$target" -v ON_ERROR_STOP=1 -q <<'SQL'
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO visacrm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO visacrm_app;
SQL

echo "restore: db/$object restored, $(psql "$target" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") tables"

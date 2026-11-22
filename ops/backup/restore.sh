#!/usr/bin/env bash
# Restore a database backup made by backup.sh (docs/backup.md → «Восстановление»):
#
#   restore.sh latest|daily/NAME.dump.age|monthly/NAME.dump.age TARGET_DATABASE_URL
#
# Only into an empty database: one that already has tables is refused, so a restore can
# never overwrite live data. Needs BACKUP_AGE_IDENTITY (the private key file, kept
# outside Railway) and the BACKUP_S3_* variables; the target URL carries no password, it
# comes from PGPASSWORD or PGPASSFILE. Files are restored separately with rclone
# (docs/backup.md).
#
# The backup key lives in Railway and may write to the bucket, so a copy is restored only
# when it looks like one backup.sh made: a name with its UTC time, not in the future,
# uploaded soon after that time by the bucket's own clock, and a single version. `latest`
# is the newest such copy; anything else in db/daily/ is skipped with a warning.
#
# The restored database keeps no login sessions, so everyone logs in again; blocks,
# password resets and erasures made after the copy are applied again by hand before the
# site is switched to it (docs/backup.md, section 4).
#
# Exit codes: 2 usage or settings, 3 the target has tables, 4 no such copy, 5 copy refused.
set -euo pipefail

# backup.sh names a copy by the time it starts; the upload follows the dump.
NAME_PATTERN='^visacrm-([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z\.dump\.age$'
MAX_UPLOAD_DELAY=$((6 * 3600))
# The clocks of this machine, Railway and the bucket may differ a little.
CLOCK_SKEW=300
export TZ=UTC

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
now=$(date +%s)

# The target is a command-line argument, which every user of the machine sees in the
# process list: the password comes from PGPASSWORD or a .pgpass file instead. The URL is
# never printed, since it may hold the password.
url_password='^[a-z]+://[^/?#@]*:[^/?#@]*@'
conninfo_password='(^|[?&[:space:]])password='
if [[ $target =~ $url_password || $target =~ $conninfo_password ]]; then
  echo "restore: the target URL holds the database password; give the URL without it and the password in PGPASSWORD or a .pgpass file (PGPASSFILE)" >&2
  exit 2
fi

# Seconds since 1970 for the time in a copy's name; fails for a name off the pattern.
taken_at() {
  [[ $1 =~ $NAME_PATTERN ]] || return 1
  local stamp="${BASH_REMATCH[1]}${BASH_REMATCH[2]}${BASH_REMATCH[3]}T${BASH_REMATCH[4]}${BASH_REMATCH[5]}${BASH_REMATCH[6]}Z"
  local seconds
  seconds=$(date -d "${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]} ${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:${BASH_REMATCH[6]}" +%s 2>/dev/null) ||
    return 1
  # A date such as 20261131 would roll over to another day.
  [ "$(date -d "@$seconds" +%Y%m%dT%H%M%SZ)" = "$stamp" ] || return 1
  echo "$seconds"
}

# Why a copy uploaded at UPLOADED (seconds) does not look like one backup.sh made; nothing
# when it does.
suspicion() {
  local name=$1 uploaded=$2 taken
  if ! taken=$(taken_at "$name"); then
    echo "its name is not visacrm-YYYYMMDDTHHMMSSZ.dump.age"
  elif [ "$taken" -gt $((now + CLOCK_SKEW)) ]; then
    echo "its time is in the future"
  elif [ "$uploaded" -lt $((taken - CLOCK_SKEW)) ] ||
    [ "$uploaded" -gt $((taken + MAX_UPLOAD_DELAY)) ]; then
    echo "it was uploaded at $(date -d "@$uploaded" '+%F %T') UTC, not when its name says"
  fi
}

# "upload time;name" of every copy in a folder, the time as the bucket recorded it.
uploads() {
  rclone lsf --files-only --use-server-modtime --format tp "dest:$BACKUP_S3_BUCKET/db/$1/"
}

if [ "$object" = latest ]; then
  folder=daily
  name=
  listing=$(uploads daily)
  while IFS=';' read -r time file; do
    [ -n "$file" ] || continue
    why=$(suspicion "$file" "$(date -d "$time" +%s)")
    if [ -n "$why" ]; then
      echo "restore: skipped db/daily/$file: $why" >&2
    elif [[ $file > $name ]]; then
      name=$file
      uploaded=$time
    fi
  done <<< "$listing"
  if [ -z "$name" ]; then
    echo "restore: no daily copy in the bucket looks like one backup.sh made" >&2
    exit 4
  fi
elif [[ $object =~ ^(daily|monthly)/([^/]+)$ ]]; then
  folder=${BASH_REMATCH[1]}
  name=${BASH_REMATCH[2]}
  uploaded=$(uploads "$folder" | awk -F';' -v name="$name" '$2 == name { print $1 }')
  if [ -z "$uploaded" ]; then
    echo "restore: db/$object is not in the bucket" >&2
    exit 4
  fi
  why=$(suspicion "$name" "$(date -d "$uploaded" +%s)")
  if [ -n "$why" ]; then
    echo "restore: db/$object is refused: $why" >&2
    exit 5
  fi
else
  echo "usage: restore.sh latest|daily/NAME|monthly/NAME TARGET_DATABASE_URL" >&2
  exit 2
fi
object="$folder/$name"
echo "restore: taking db/$object, uploaded $uploaded UTC"

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

# A file uploaded again under the name of a copy is a planted one: the bucket keeps every
# version, so the copy must have exactly one. Checked after the download, so a version
# added meanwhile is caught too. A storage without versions (the S3 server of the CI test)
# is accepted only with BACKUP_S3_VERSIONS=none.
if versions=$(rclone lsf --files-only --s3-versions "dest:$BACKUP_S3_BUCKET/db/$folder/"); then
  # rclone shows an old version with -vYYYY-MM-DD-HHMMSS-mmm added to the name.
  count=$(sed -E 's/-v[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}-[0-9]{3}//' <<< "$versions" |
    grep -cxF "$name" || true)
  if [ "$count" != 1 ]; then
    echo "restore: db/$object has $count versions in the bucket: a file was uploaded again under its name; restore refused, take another copy (docs/backup.md, section 4)" >&2
    exit 5
  fi
elif [ "${BACKUP_S3_VERSIONS:-}" = none ]; then
  echo "restore: versions not checked: the storage keeps none (BACKUP_S3_VERSIONS=none)" >&2
else
  echo "restore: could not list the versions of db/$object; restore refused" >&2
  exit 5
fi

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
-- Nobody stays signed in from the copy: a session of someone blocked since would work again.
DELETE FROM user_sessions;
SQL

echo "restore: db/$object restored, $(psql "$target" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") tables; login sessions deleted, everyone logs in again"
echo "restore: before switching the site to this database, apply again the blocks, password resets and erasures made after $(date -d "@$(taken_at "$name")" '+%F %T') UTC (docs/backup.md, section 4)" >&2

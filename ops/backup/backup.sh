#!/usr/bin/env bash
# Daily backup of production (backlog 0.11 and S21, docs/backup.md), run by a Railway cron
# service next to the web service:
#   1. pg_dump of the database, encrypted with age for BACKUP_AGE_RECIPIENT — the private
#      key never reaches Railway — and uploaded to the external bucket under db/daily/,
#      on the 1st of the month also under db/monthly/. The bucket locks every new file for
#      30 days in compliance mode; a monthly copy lives a year, so it gets its own
#      compliance lock for its whole life. How long copies live is set by the bucket's
#      lifecycle rules.
#   2. The app's files bucket (consents, contracts, templates — already encrypted with
#      each specialist's key) mirrored to files/. A file deleted in the app is only hidden
#      in the mirror; the lifecycle deletes it 30 days later.
# The service holds two keys of the backup bucket, each limited to its own folder, and
# neither can delete a stored version, shorten a lock or change the bucket (docs/backup.md,
# section 2): BACKUP_S3_DB_* writes and locks db/, BACKUP_S3_FILES_* writes and hides in
# files/. So whoever reads the service's variables in Railway cannot destroy the copies.
# Nothing unencrypted is written to disk. Any failure exits non-zero, so the cron run is
# marked failed; BACKUP_HEARTBEAT_URL, if set, is called only after a full success, so a
# monitor notices when backups stop coming.
#
# Exit codes: 2 settings missing; 78 (EX_CONFIG) the copy is made, but the service still
# holds the old single key BACKUP_S3_ACCESS_KEY (docs/backup.md, section 6); any other is
# the code of the step that failed (pg_dump, age, rclone).
set -euo pipefail

# Lifecycle rules hide a monthly copy 365 days after the upload and delete it a day later:
# its lock lasts that whole life. Shorter only on a test bucket (docs/backup.md, section 6).
monthly_lock_days=${BACKUP_MONTHLY_LOCK_DAYS:-366}

need() {
  local name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "backup: $name is not set" >&2
      exit 2
    fi
  done
}

need DATABASE_URL BACKUP_AGE_RECIPIENT BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET
# Files are part of the backup unless switched off on purpose: a missing variable must
# not silently leave the documents out.
files=${BACKUP_FILES:-on}
if [ "$files" != off ]; then
  need S3_ENDPOINT S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY
fi
if ! [[ $monthly_lock_days =~ ^[1-9][0-9]{0,3}$ ]]; then
  echo "backup: BACKUP_MONTHLY_LOCK_DAYS is not a number of days" >&2
  exit 2
fi

# Until S21 one Read and Write key, BACKUP_S3_ACCESS_KEY, did everything, and it could
# delete copies and change the bucket. While the service has only that key, the copy is
# still made with it (a day without a copy is worse), and the run then ends failed.
if [ -z "${BACKUP_S3_DB_ACCESS_KEY:-}${BACKUP_S3_FILES_ACCESS_KEY:-}" ] &&
  [ -n "${BACKUP_S3_ACCESS_KEY:-}" ]; then
  need BACKUP_S3_SECRET_KEY
  db_id=$BACKUP_S3_ACCESS_KEY db_secret=$BACKUP_S3_SECRET_KEY
  files_id=$BACKUP_S3_ACCESS_KEY files_secret=$BACKUP_S3_SECRET_KEY
else
  need BACKUP_S3_DB_ACCESS_KEY BACKUP_S3_DB_SECRET_KEY
  db_id=$BACKUP_S3_DB_ACCESS_KEY db_secret=$BACKUP_S3_DB_SECRET_KEY
  if [ "$files" != off ]; then
    need BACKUP_S3_FILES_ACCESS_KEY BACKUP_S3_FILES_SECRET_KEY
    files_id=$BACKUP_S3_FILES_ACCESS_KEY files_secret=$BACKUP_S3_FILES_SECRET_KEY
  fi
fi
. "$(dirname "$0")/remotes.sh"
# The remote "dest" of remotes.sh, with one of the keys.
use_key() {
  export RCLONE_CONFIG_DEST_ACCESS_KEY_ID=$1 RCLONE_CONFIG_DEST_SECRET_ACCESS_KEY=$2
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
name="visacrm-$(date -u +%Y%m%dT%H%M%SZ).dump.age"

pg_dump --format=custom --dbname="$DATABASE_URL" |
  age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$work/$name"
size=$(stat -c %s "$work/$name")
use_key "$db_id" "$db_secret"
rclone copyto "$work/$name" "dest:$BACKUP_S3_BUCKET/db/daily/$name"
echo "backup: db/daily/$name, $size bytes"
if [ "${BACKUP_MONTHLY:-auto}" = always ] || [ "$(date -u +%d)" = 01 ]; then
  # Set by a separate call right after the upload (PutObjectRetention), which the bucket
  # takes for a file of any size; if it refuses, rclone fails and so does the run.
  until=$(date -u -d "+$monthly_lock_days days" +%Y-%m-%dT%H:%M:%SZ)
  rclone copyto --s3-object-lock-mode COMPLIANCE --s3-object-lock-retain-until-date "$until" \
    --s3-object-lock-set-after-upload "$work/$name" "dest:$BACKUP_S3_BUCKET/db/monthly/$name"
  echo "backup: db/monthly/$name, locked until $until"
fi

if [ "$files" = off ]; then
  echo "backup: files skipped (BACKUP_FILES=off)"
else
  use_key "$files_id" "$files_secret"
  # The trailing slash keeps rclone from looking up a file named "files", which a key
  # limited to files/ is not allowed to read.
  rclone sync "files:$S3_BUCKET" "dest:$BACKUP_S3_BUCKET/files/"
  echo "backup: files mirrored, $(rclone size "dest:$BACKUP_S3_BUCKET/files/" | tr '\n' ' ')"
fi

if [ -n "${BACKUP_S3_ACCESS_KEY:-}" ]; then
  echo "backup: the copy is made, but the service still holds BACKUP_S3_ACCESS_KEY, the old key that can delete copies and change the bucket: give it BACKUP_S3_DB_* and BACKUP_S3_FILES_*, remove BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY, then delete the old key in B2 (docs/backup.md, section 6)" >&2
  exit 78
fi

if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
  curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_HEARTBEAT_URL" ||
    echo "backup: done, but the heartbeat did not answer" >&2
fi

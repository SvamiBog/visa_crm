#!/usr/bin/env bash
# Daily backup of production (backlog 0.11, docs/backup.md), run by a Railway cron
# service next to the web service:
#   1. pg_dump of the database, encrypted with age for BACKUP_AGE_RECIPIENT — the private
#      key never reaches Railway — and uploaded to the external bucket under db/daily/,
#      on the 1st of the month also under db/monthly/. How long copies live is set by the
#      bucket's lifecycle rules and Object Lock, not here.
#   2. The app's files bucket (consents, contracts, templates — already encrypted with
#      each specialist's key) mirrored to files/; the bucket keeps versions, so a file
#      deleted in the app stays in the copy until the lifecycle removes it.
# Nothing unencrypted is written to disk. Any failure exits non-zero, so the cron run is
# marked failed; BACKUP_HEARTBEAT_URL, if set, is called only after a full success, so a
# monitor notices when backups stop coming.
set -euo pipefail

need() {
  local name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "backup: $name is not set" >&2
      exit 2
    fi
  done
}

need DATABASE_URL BACKUP_AGE_RECIPIENT \
  BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY
# Files are part of the backup unless switched off on purpose: a missing variable must
# not silently leave the documents out.
files=${BACKUP_FILES:-on}
if [ "$files" != off ]; then
  need S3_ENDPOINT S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY
fi
. "$(dirname "$0")/remotes.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
name="visacrm-$(date -u +%Y%m%dT%H%M%SZ).dump.age"

pg_dump --format=custom --dbname="$DATABASE_URL" |
  age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$work/$name"
size=$(stat -c %s "$work/$name")
rclone copyto "$work/$name" "dest:$BACKUP_S3_BUCKET/db/daily/$name"
if [ "${BACKUP_MONTHLY:-auto}" = always ] || [ "$(date -u +%d)" = 01 ]; then
  rclone copyto "$work/$name" "dest:$BACKUP_S3_BUCKET/db/monthly/$name"
fi
echo "backup: db/daily/$name, $size bytes"

if [ "$files" = off ]; then
  echo "backup: files skipped (BACKUP_FILES=off)"
else
  rclone sync "files:$S3_BUCKET" "dest:$BACKUP_S3_BUCKET/files"
  echo "backup: files mirrored, $(rclone size "dest:$BACKUP_S3_BUCKET/files" | tr '\n' ' ')"
fi

if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
  curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_HEARTBEAT_URL" ||
    echo "backup: done, but the heartbeat did not answer" >&2
fi

#!/usr/bin/env bash
# CI check of the backup (backlog 0.11, docs/backup.md): the backup image dumps the
# database with demo data, encrypts it and mirrors a files bucket into a throwaway S3
# server; the dump is restored into an empty database and compared with the source table
# by table — rows, Row-Level Security, policies, grants of visacrm_app and triggers.
#
# Run from the repository root on a host with Docker and the CI Postgres on
# localhost:5432 (user visacrm, source database visacrm with demo data).
set -euo pipefail

IMAGE=visacrm-backup:ci
S3_SERVER_IMAGE=rclone/rclone:1.68.2
PG=postgresql://visacrm:visacrm@localhost:5432
SOURCE=$PG/visacrm
TARGET=$PG/visacrm_restored

work=$(mktemp -d)
# The image runs as postgres, not as the runner's user.
chmod 777 "$work"
cleanup() {
  docker rm -f backup-s3 >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

docker build --quiet --build-arg PG_MAJOR=16 -t "$IMAGE" ops/backup >/dev/null

# A local S3 server for both buckets: the app's files and the external backup.
docker run -d --name backup-s3 --network host "$S3_SERVER_IMAGE" \
  serve s3 --addr 127.0.0.1:9000 --auth-key ci-access,ci-secret-key /data >/dev/null
for _ in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:9000 && break
  sleep 1
done

# A key pair made for this run only: the private half stays on the runner.
docker run --rm --entrypoint age-keygen "$IMAGE" > "$work/key.txt" 2>/dev/null
chmod 644 "$work/key.txt"
recipient=$(grep -o 'age1[0-9a-z]*' "$work/key.txt" | head -n 1)
[ -n "$recipient" ] || fail "age-keygen gave no public key"

cat > "$work/env" <<ENV
DATABASE_URL=$SOURCE
BACKUP_AGE_RECIPIENT=$recipient
BACKUP_S3_ENDPOINT=http://127.0.0.1:9000
BACKUP_S3_REGION=us-east-1
BACKUP_S3_BUCKET=visacrm-backup
BACKUP_S3_ACCESS_KEY=ci-access
BACKUP_S3_SECRET_KEY=ci-secret-key
S3_ENDPOINT=http://127.0.0.1:9000
S3_REGION=us-east-1
S3_BUCKET=visacrm-files
S3_ACCESS_KEY=ci-access
S3_SECRET_KEY=ci-secret-key
BACKUP_MONTHLY=always
ENV
run() { docker run --rm -i --network host --env-file "$work/env" -v "$work:/work" "$@"; }
# rclone inside the image, with the remotes "dest" and "files" of remotes.sh.
rclone() {
  run --entrypoint sh "$IMAGE" -c '. /usr/local/bin/remotes.sh && exec rclone "$@"' rclone "$@"
}

# Buckets of the S3 server are its directories.
docker exec backup-s3 mkdir -p /data/visacrm-backup /data/visacrm-files
printf 'encrypted-document-bytes' | rclone rcat files:visacrm-files/tenant-a/document-1

echo "--- backup"
run "$IMAGE"

echo "--- what landed in the bucket"
daily=$(rclone lsf dest:visacrm-backup/db/daily/)
monthly=$(rclone lsf dest:visacrm-backup/db/monthly/)
[ "$(echo "$daily" | grep -c '^visacrm-.*\.dump\.age$')" = 1 ] || fail "one daily dump expected, got: $daily"
[ "$daily" = "$monthly" ] || fail "the monthly copy is missing: $monthly"
rclone copyto "dest:visacrm-backup/db/daily/$daily" "/work/copy.age"
head -c 21 "$work/copy.age" | grep -q 'age-encryption.org/v1' || fail "the dump is not age-encrypted"
if grep -a -q -e PGDMP -e 'CREATE TABLE' -e 'visa_cases' "$work/copy.age"; then
  fail "plaintext of the dump is visible in the stored copy"
fi
[ "$(rclone cat dest:visacrm-backup/files/tenant-a/document-1)" = 'encrypted-document-bytes' ] ||
  fail "the files bucket was not mirrored"

echo "--- restore into an empty database"
psql "$PG/postgres" -v ON_ERROR_STOP=1 -qc 'CREATE DATABASE visacrm_restored'
run -e BACKUP_AGE_IDENTITY=/work/key.txt --entrypoint restore.sh "$IMAGE" latest "$TARGET"

echo "--- compare source and restored"
# Every table's rows, as one checksum per table; then RLS, policies, grants and triggers.
read -r -d '' COMPARE <<'SQL' || true
SELECT 'rows', table_name, (xpath('/row/c/text()', query_to_xml(format(
         'SELECT md5(coalesce(string_agg(t::text, %L ORDER BY t::text), %L)) || %L || count(*) AS c FROM %I.%I t',
         E'\n', '', ':', table_schema, table_name), false, true, '')))[1]::text
  FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
UNION ALL
SELECT 'rls', relname, relrowsecurity::text || relforcerowsecurity::text
  FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
UNION ALL
SELECT 'policy', tablename || '.' || policyname, cmd || coalesce(qual, '') || coalesce(with_check, '')
  FROM pg_policies WHERE schemaname = 'public'
UNION ALL
SELECT 'grant', table_name, string_agg(privilege_type, ',' ORDER BY privilege_type)
  FROM information_schema.role_table_grants WHERE grantee = 'visacrm_app' GROUP BY table_name
UNION ALL
SELECT 'trigger', tgrelid::regclass::text || '.' || tgname, ''
  FROM pg_trigger WHERE NOT tgisinternal
ORDER BY 1, 2;
SQL
psql "$SOURCE" -At -c "$COMPARE" > "$work/source.txt"
psql "$TARGET" -At -c "$COMPARE" > "$work/restored.txt"
diff "$work/source.txt" "$work/restored.txt" || fail "the restored database differs from the source"
tables=$(grep -c '^rows|' "$work/source.txt")
rows=$(awk -F'|' '$1 == "rows" { split($3, p, ":"); n += p[2] } END { print n }' "$work/source.txt")
[ "$rows" -gt 0 ] || fail "the source has no rows: demo data missing"
grep -q '^trigger|audit_events' "$work/restored.txt" || fail "the audit log trigger was not restored"
echo "same content in $tables tables ($rows rows), RLS, policies, grants and triggers"

echo "--- a restore never overwrites a database that has tables"
if run -e BACKUP_AGE_IDENTITY=/work/key.txt --entrypoint restore.sh "$IMAGE" latest "$TARGET"; then
  fail "restore went into a non-empty database"
fi
echo "OK"

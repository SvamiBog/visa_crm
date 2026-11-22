#!/usr/bin/env bash
# CI check of the backup (backlog 0.11 and S11, docs/backup.md): the backup image dumps the
# database with demo data, encrypts it and mirrors a files bucket into a throwaway S3
# server. The dump is restored as the monthly check does it: into a separate Postgres
# container with no network and no TCP port, reached only through a socket volume. It is
# compared with the source table by table — rows, Row-Level Security, policies, grants of
# visacrm_app and triggers — and must come back without the login sessions. Copies planted
# in the bucket are never taken for `latest`, a copy with several versions is refused, and
# so is a database password in the target URL.
#
# Run from the repository root on a host with Docker and the CI Postgres on
# localhost:5432 (user visacrm, source database visacrm with demo data).
set -euo pipefail

IMAGE=visacrm-backup:ci
S3_SERVER_IMAGE=rclone/rclone:1.68.2
# The restore target of the monthly check: the major version of production.
DRILL_IMAGE=postgres:18
DRILL=visacrm-restore-drill
DRILL_SOCKET=visacrm-restore-drill-socket
# The password of the CI Postgres goes through PGPASSWORD, never in a command line.
export PGPASSWORD=visacrm
PG=postgresql://visacrm@localhost:5432
SOURCE=$PG/visacrm
TARGET=$PG/visacrm_restored

work=$(mktemp -d)
# The image runs as postgres, not as the runner's user.
chmod 777 "$work"
cleanup() {
  docker rm -f backup-s3 "$DRILL" >/dev/null 2>&1 || true
  docker volume rm "$DRILL_SOCKET" >/dev/null 2>&1 || true
  psql "$SOURCE" -qc "DELETE FROM user_sessions WHERE token_hash LIKE 'roundtrip-%'" \
    >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

docker build --quiet --build-arg PG_MAJOR=18 -t "$IMAGE" ops/backup >/dev/null

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

# The test S3 server keeps no versions of objects, which restore.sh accepts only when told.
cat > "$work/env" <<ENV
DATABASE_URL=$SOURCE
PGPASSWORD=$PGPASSWORD
BACKUP_AGE_RECIPIENT=$recipient
BACKUP_S3_ENDPOINT=http://127.0.0.1:9000
BACKUP_S3_REGION=us-east-1
BACKUP_S3_BUCKET=visacrm-backup
BACKUP_S3_ACCESS_KEY=ci-access
BACKUP_S3_SECRET_KEY=ci-secret-key
BACKUP_S3_VERSIONS=none
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

echo "--- login sessions in the source, which a restore must not bring back"
psql "$SOURCE" -v ON_ERROR_STOP=1 -qc "INSERT INTO user_sessions (id, user_id, token_hash, expires_at)
  SELECT gen_random_uuid(), id, 'roundtrip-' || md5(random()::text), now() + interval '1 day'
  FROM users"
source_sessions=$(psql "$SOURCE" -Atc 'SELECT count(*) FROM user_sessions')
[ "$source_sessions" -gt 0 ] || fail "the source has no users to sign in"

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

echo "--- the restore target: a separate Postgres with no network and no TCP port"
docker run -d --name "$DRILL" --network none -v "$DRILL_SOCKET:/var/run/postgresql" \
  -e POSTGRES_PASSWORD="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" \
  "$DRILL_IMAGE" -c listen_addresses= >/dev/null
# The image starts a temporary server for its first setup: wait for the real one.
for _ in $(seq 1 60); do
  docker logs "$DRILL" 2>&1 | grep -q 'init process complete' &&
    docker exec "$DRILL" pg_isready -q && break
  sleep 1
done
docker exec "$DRILL" pg_isready -q || fail "the restore target did not start"
drill_psql() { docker exec -i "$DRILL" psql -U postgres -v ON_ERROR_STOP=1 "$@"; }
[ -z "$(drill_psql -Atc 'SHOW listen_addresses')" ] || fail "the restore target listens on TCP"
drill_psql -qc 'CREATE DATABASE restored'
drill_psql -qc 'CREATE DATABASE versions_check'
# restore.sh runs as postgres in the image and reaches the target through the socket only.
drill_restore() {
  run -v "$DRILL_SOCKET:/var/run/postgresql" -e BACKUP_AGE_IDENTITY=/work/key.txt \
    --entrypoint restore.sh "$@"
}
drill_url() { echo "postgresql:///$1?host=/var/run/postgresql"; }

echo "--- copies planted in the bucket: a name from the future and a name off the pattern"
drill_psql -qc 'CREATE DATABASE planted'
drill_psql -d planted -qc 'CREATE TABLE planted_marker ()'
docker exec "$DRILL" pg_dump -U postgres --format=custom planted > "$work/planted.dump"
run --entrypoint age "$IMAGE" --encrypt --recipient "$recipient" \
  --output /work/planted.age /work/planted.dump
for name in visacrm-99991231T235959Z.dump.age visacrm-zzz.dump.age; do
  rclone copyto /work/planted.age "dest:visacrm-backup/db/daily/$name"
done

echo "--- restore latest into the separate Postgres"
drill_restore "$IMAGE" latest "$(drill_url restored)" 2>&1 | tee "$work/restore.log"
[ "$(drill_psql -d restored -Atc "SELECT to_regclass('planted_marker') IS NULL")" = t ] ||
  fail "a planted copy was restored as latest"
grep -q "^restore: db/daily/$daily restored" "$work/restore.log" ||
  fail "latest is not the genuine copy $daily"
restored_sessions=$(drill_psql -d restored -Atc 'SELECT count(*) FROM user_sessions')
[ "$restored_sessions" = 0 ] ||
  fail "the restore brought back $restored_sessions of $source_sessions login sessions"

echo "--- compare source and restored"
# Every table's rows, as one checksum per table; then RLS, policies, grants and triggers.
# The login sessions are left out: the restore deletes them on purpose (checked above).
read -r -d '' COMPARE <<'SQL' || true
SELECT 'rows', table_name, (xpath('/row/c/text()', query_to_xml(format(
         'SELECT md5(coalesce(string_agg(t::text, %L ORDER BY t::text), %L)) || %L || count(*) AS c FROM %I.%I t',
         E'\n', '', ':', table_schema, table_name), false, true, '')))[1]::text
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'user_sessions'
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
drill_psql -d restored -At -c "$COMPARE" > "$work/restored.txt"
diff "$work/source.txt" "$work/restored.txt" || fail "the restored database differs from the source"
tables=$(grep -c '^rows|' "$work/source.txt")
rows=$(awk -F'|' '$1 == "rows" { split($3, p, ":"); n += p[2] } END { print n }' "$work/source.txt")
[ "$rows" -gt 0 ] || fail "the source has no rows: demo data missing"
grep -q '^trigger|audit_events' "$work/restored.txt" || fail "the audit log trigger was not restored"
echo "same content in $tables tables ($rows rows), RLS, policies, grants and triggers"

echo "--- a copy with several versions is refused"
# B2 keeps versions, the test server does not: this rclone lists every copy twice, once
# as an old version, the way rclone shows the versions of a B2 bucket.
mkdir "$work/versioned"
cat > "$work/versioned/rclone" <<'STUB'
#!/usr/bin/env bash
args=()
versions=no
for arg in "$@"; do
  if [ "$arg" = --s3-versions ]; then versions=yes; else args+=("$arg"); fi
done
[ "$versions" = yes ] || exec /usr/bin/rclone "$@"
/usr/bin/rclone "${args[@]}" | sed -E 'p; s/\.dump\.age$/.dump-v2026-01-01-000000-000.age/'
STUB
chmod 755 "$work/versioned/rclone"
image_path=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" |
  sed -n 's/^PATH=//p')
if drill_restore -e "PATH=/work/versioned:$image_path" "$IMAGE" \
  "daily/$daily" "$(drill_url versions_check)" 2> "$work/versions.log"; then
  fail "a copy with several versions was restored"
fi
cat "$work/versions.log" >&2
grep -q 'versions' "$work/versions.log" || fail "the refusal does not name the versions"
[ "$(drill_psql -d versions_check -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")" = 0 ] ||
  fail "something was restored from a copy with several versions"

echo "--- a planted copy is refused when named, too"
if drill_restore "$IMAGE" daily/visacrm-99991231T235959Z.dump.age "$(drill_url versions_check)"; then
  fail "a copy from the future was restored by its name"
fi
[ "$(drill_psql -d versions_check -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")" = 0 ] ||
  fail "something was restored from a planted copy"

echo "--- the database password never goes in the command line"
psql "$PG/postgres" -v ON_ERROR_STOP=1 -qc 'DROP DATABASE IF EXISTS visacrm_restored'
psql "$PG/postgres" -v ON_ERROR_STOP=1 -qc 'CREATE DATABASE visacrm_restored'
if run -e BACKUP_AGE_IDENTITY=/work/key.txt --entrypoint restore.sh "$IMAGE" \
  latest "postgresql://visacrm:$PGPASSWORD@localhost:5432/visacrm_restored" 2> "$work/password.log"; then
  fail "restore took a target URL with the password in it"
fi
cat "$work/password.log" >&2
grep -q PGPASSWORD "$work/password.log" || fail "the refusal does not point to PGPASSWORD"
[ "$(psql "$TARGET" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")" = 0 ] ||
  fail "something was restored through a URL with the password"

echo "--- restore over TCP with the password in PGPASSWORD, as into a new database in Railway"
# The CI step after this script checks the revision of visacrm_restored.
run -e BACKUP_AGE_IDENTITY=/work/key.txt --entrypoint restore.sh "$IMAGE" latest "$TARGET"
[ "$(psql "$TARGET" -Atc 'SELECT count(*) FROM user_sessions')" = 0 ] ||
  fail "the restore over TCP brought back login sessions"

echo "--- a restore never overwrites a database that has tables"
if run -e BACKUP_AGE_IDENTITY=/work/key.txt --entrypoint restore.sh "$IMAGE" latest "$TARGET"; then
  fail "restore went into a non-empty database"
fi
echo "OK"

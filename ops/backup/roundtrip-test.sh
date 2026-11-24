#!/usr/bin/env bash
# CI check of the backup (backlog 0.11, S11 and S21, docs/backup.md): the backup image dumps
# the database with demo data, encrypts it and mirrors a files bucket into a throwaway S3
# server set up like the B2 bucket: versions, Object Lock and a key per folder with the
# rights of docs/backup.md, section 2. The dump is restored as the monthly check does it:
# into a separate Postgres container with no network and no TCP port, reached only through
# a socket volume. It is compared with the source table by table — rows, Row-Level
# Security, policies, grants of visacrm_app and triggers — and must come back without the
# login sessions. Copies planted in the bucket are never taken for `latest`, a copy with
# several versions is refused, and so is a database password in the target URL.
#
# The keys (S21): the monthly copy comes with a compliance lock for a year that not even
# the owner can lift; each key of the backup service writes only its own folder and
# deletes no stored version; a file deleted in the app stays in the mirror as a hidden
# version; the old single key still makes the copy, but the run fails.
#
# Run from the repository root on a host with Docker and the CI Postgres on
# localhost:5432 (user visacrm, source database visacrm with demo data).
set -euo pipefail
# A listing is read into a variable before grep -q looks in it: grep -q stops at the first
# match, and under pipefail the listing's broken pipe would fail the pipe however the
# match went.

IMAGE=visacrm-backup:ci
# Unlike rclone's own S3 server it keeps versions, takes Object Lock and checks each key's
# rights.
S3_SERVER_IMAGE=versity/versitygw:v1.5.0@sha256:7d56e0a6c246f00e2385ba4fe84065981959be1d822648f88a155c82e0e0652d
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
  docker rm -f -v backup-s3 "$DRILL" >/dev/null 2>&1 || true
  docker volume rm "$DRILL_SOCKET" >/dev/null 2>&1 || true
  psql "$SOURCE" -qc "DELETE FROM user_sessions WHERE token_hash LIKE 'roundtrip-%'" \
    >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

docker build --quiet --build-arg PG_MAJOR=18 -t "$IMAGE" ops/backup >/dev/null

# A local S3 server for both buckets: the app's files and the external backup. Its root
# key plays the owner of the B2 account.
docker run -d --name backup-s3 --network host -v /data -v /versions -v /iam \
  -e ROOT_ACCESS_KEY=ci-access -e ROOT_SECRET_KEY=ci-secret-key "$S3_SERVER_IMAGE" \
  --port 127.0.0.1:9000 --admin-port 127.0.0.1:9001 --iam-dir /iam \
  posix --versioning-dir /versions /data >/dev/null
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
PGPASSWORD=$PGPASSWORD
BACKUP_AGE_RECIPIENT=$recipient
BACKUP_S3_ENDPOINT=http://127.0.0.1:9000
BACKUP_S3_REGION=us-east-1
BACKUP_S3_BUCKET=visacrm-backup
S3_ENDPOINT=http://127.0.0.1:9000
S3_REGION=us-east-1
S3_BUCKET=visacrm-files
S3_ACCESS_KEY=ci-access
S3_SECRET_KEY=ci-secret-key
BACKUP_MONTHLY=always
ENV
# The keys of the backup bucket, as BACKUP_S3_ACCESS_KEY of the remote "dest": the owner,
# the two keys of the backup service and the read-only key of a restore.
key_env() {
  printf 'BACKUP_S3_ACCESS_KEY=%s\nBACKUP_S3_SECRET_KEY=%s\n' "$2" "$3" > "$work/$1.env"
}
key_env owner ci-access ci-secret-key
key_env db ci-db ci-db-secret
key_env files ci-files ci-files-secret
key_env restore ci-restore ci-restore-secret
# The backup service has only its two keys.
cat > "$work/backup.env" <<ENV
BACKUP_S3_DB_ACCESS_KEY=ci-db
BACKUP_S3_DB_SECRET_KEY=ci-db-secret
BACKUP_S3_FILES_ACCESS_KEY=ci-files
BACKUP_S3_FILES_SECRET_KEY=ci-files-secret
ENV
run() { docker run --rm -i --network host --env-file "$work/env" -v "$work:/work" "$@"; }
# rclone inside the image with one of the keys, and the remotes "dest" and "files" of
# remotes.sh; plain rclone is the owner.
rclone_as() {
  local key=$1
  shift
  run --env-file "$work/$key.env" --entrypoint sh "$IMAGE" \
    -c '. /usr/local/bin/remotes.sh && exec rclone "$@"' rclone "$@"
}
rclone() { rclone_as owner "$@"; }
backup() { run --env-file "$work/backup.env" "$@" "$IMAGE"; }
restore() {
  run --env-file "$work/restore.env" -e BACKUP_AGE_IDENTITY=/work/key.txt \
    --entrypoint restore.sh "$@"
}

# The backup bucket with Object Lock, as in B2, and the app's files bucket.
rclone mkdir --s3-bucket-object-lock-enabled 'dest,no_check_bucket=false:visacrm-backup'
rclone mkdir 'files,no_check_bucket=false:visacrm-files'
for key in ci-db ci-files ci-restore; do
  docker exec backup-s3 versitygw admin --access ci-access --secret ci-secret-key \
    --endpoint-url http://127.0.0.1:9001 create-user --access "$key" --secret "$key-secret" \
    --role user >/dev/null
done
# What each key may do, as the B2 keys of docs/backup.md, section 2: in db/ write and lock,
# but not delete; in files/ write and hide (a delete without a version, B2's writeFiles),
# but not delete a version (B2's deleteFiles); the restore key only reads. Unlike B2, the
# listing is not limited to the key's folder here.
cat > "$work/policy.json" <<'JSON'
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Principal": {"AWS": ["ci-db", "ci-files", "ci-restore"]},
   "Action": ["s3:ListBucket", "s3:ListBucketVersions"], "Resource": "arn:aws:s3:::visacrm-backup"},
  {"Effect": "Allow", "Principal": {"AWS": ["ci-db"]},
   "Action": ["s3:PutObject", "s3:GetObject", "s3:PutObjectRetention", "s3:GetObjectRetention"],
   "Resource": "arn:aws:s3:::visacrm-backup/db/*"},
  {"Effect": "Allow", "Principal": {"AWS": ["ci-files"]},
   "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
   "Resource": "arn:aws:s3:::visacrm-backup/files/*"},
  {"Effect": "Allow", "Principal": {"AWS": ["ci-restore"]},
   "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::visacrm-backup/*"}
]}
JSON
# rclone cannot set a bucket policy: curl signs the call with the owner's key.
run --env-file "$work/owner.env" --entrypoint bash "$IMAGE" -c '
  printf "user = \"%s:%s\"\n" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY" |
    curl -fsS -K - --aws-sigv4 "aws:amz:$BACKUP_S3_REGION:s3" -X PUT \
      -H "x-amz-content-sha256: $(sha256sum < /work/policy.json | cut -d" " -f1)" \
      --data-binary @/work/policy.json "$BACKUP_S3_ENDPOINT/$BACKUP_S3_BUCKET?policy="'
printf 'encrypted-document-bytes' | rclone rcat files:visacrm-files/tenant-a/document-1

echo "--- login sessions in the source, which a restore must not bring back"
psql "$SOURCE" -v ON_ERROR_STOP=1 -qc "INSERT INTO user_sessions (id, user_id, token_hash, expires_at)
  SELECT gen_random_uuid(), id, 'roundtrip-' || md5(random()::text), now() + interval '1 day'
  FROM users"
source_sessions=$(psql "$SOURCE" -Atc 'SELECT count(*) FROM user_sessions')
[ "$source_sessions" -gt 0 ] || fail "the source has no users to sign in"

echo "--- backup with the two keys of the backup service"
backup

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

echo "--- the monthly copy is locked for its whole life in compliance mode, the daily one is not"
lock=$(rclone lsjson -M "dest:visacrm-backup/db/monthly/$monthly")
grep -q '"object-lock-mode":"COMPLIANCE"' <<< "$lock" ||
  fail "the monthly copy is not locked in compliance mode: $lock"
until=$(grep -o '"object-lock-retain-until-date":"[^"]*"' <<< "$lock" | cut -d'"' -f4)
days=$((($(date -u -d "$until" +%s) - $(date -u +%s)) / 86400))
[ "$days" -ge 365 ] && [ "$days" -le 366 ] ||
  fail "the monthly copy is locked until $until, not for a year"
# A daily copy has only the bucket's lock of 30 days (none on the test server).
daily_lock=$(rclone lsjson -M "dest:visacrm-backup/db/daily/$daily")
if grep -q object-lock-mode <<< "$daily_lock"; then
  fail "the daily copy has a lock of its own"
fi

echo "--- nobody deletes the monthly copy, not even the owner who may bypass governance"
if rclone_as db deletefile "dest:visacrm-backup/db/monthly/$monthly" 2>/dev/null; then
  fail "the db key deleted the monthly copy"
fi
# The owner hides the copy, then deletes its stored version: the lock refuses.
rclone deletefile "dest:visacrm-backup/db/monthly/$monthly"
version=$(rclone lsf --s3-versions dest:visacrm-backup/db/monthly/ | grep "^${monthly%.age}-v") ||
  fail "the hidden monthly copy has no stored version"
if rclone deletefile --s3-versions --s3-bypass-governance-retention \
  "dest:visacrm-backup/db/monthly/$version" 2>/dev/null; then
  fail "the owner deleted the locked monthly copy"
fi
monthly_versions=$(rclone lsf --s3-versions dest:visacrm-backup/db/monthly/)
grep -qxF "$version" <<< "$monthly_versions" || fail "the locked monthly copy is gone"

echo "--- each key of the backup service writes only its own folder"
printf 'stray' > "$work/stray"
if rclone_as db copyto /work/stray dest:visacrm-backup/files/stray 2>/dev/null; then
  fail "the db key wrote into files/"
fi
if rclone_as files copyto /work/stray dest:visacrm-backup/db/daily/stray 2>/dev/null; then
  fail "the files key wrote into db/"
fi

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
drill_restore() { restore -v "$DRILL_SOCKET:/var/run/postgresql" "$@"; }
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
# Without touching the genuine copy, which later restores take: this rclone lists every
# copy twice, once as an old version, the way rclone shows a file uploaded again under the
# same name.
mkdir "$work/versioned"
cat > "$work/versioned/rclone" <<'STUB'
#!/usr/bin/env bash
args=()
versions=no
for arg in "$@"; do
  if [ "$arg" = --s3-versions ]; then versions=yes; else args+=("$arg"); fi
done
[ "$versions" = yes ] || exec /usr/local/bin/rclone "$@"
/usr/local/bin/rclone "${args[@]}" | sed -E 'p; s/\.dump\.age$/.dump-v2026-01-01-000000-000.age/'
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
if restore "$IMAGE" latest "postgresql://visacrm:$PGPASSWORD@localhost:5432/visacrm_restored" \
  2> "$work/password.log"; then
  fail "restore took a target URL with the password in it"
fi
cat "$work/password.log" >&2
grep -q PGPASSWORD "$work/password.log" || fail "the refusal does not point to PGPASSWORD"
[ "$(psql "$TARGET" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")" = 0 ] ||
  fail "something was restored through a URL with the password"

echo "--- restore over TCP with the password in PGPASSWORD, as into a new database in Railway"
# The CI step after this script checks the revision of visacrm_restored.
restore "$IMAGE" latest "$TARGET"
[ "$(psql "$TARGET" -Atc 'SELECT count(*) FROM user_sessions')" = 0 ] ||
  fail "the restore over TCP brought back login sessions"

echo "--- a restore never overwrites a database that has tables"
if restore "$IMAGE" latest "$TARGET"; then
  fail "restore went into a non-empty database"
fi

# The checks below add copies to db/daily/, so they come after the restores.
echo "--- a file deleted in the app is only hidden in the mirror, and its key cannot delete it"
rclone deletefile files:visacrm-files/tenant-a/document-1
printf 'encrypted-document-bytes' | rclone rcat files:visacrm-files/tenant-a/document-2
backup -e BACKUP_MONTHLY=auto
[ "$(rclone lsf dest:visacrm-backup/files/tenant-a/)" = document-2 ] ||
  fail "the mirror does not follow the files bucket"
hidden=$(rclone lsf --s3-versions dest:visacrm-backup/files/tenant-a/ | grep '^document-1-v') ||
  fail "the deleted file is gone from the mirror instead of hidden"
if rclone_as files deletefile --s3-versions "dest:visacrm-backup/files/tenant-a/$hidden" 2>/dev/null; then
  fail "the files key deleted a stored version"
fi
files_versions=$(rclone lsf --s3-versions dest:visacrm-backup/files/tenant-a/)
grep -qxF "$hidden" <<< "$files_versions" || fail "the hidden version of the deleted file is gone"

echo "--- the old single key still makes the copy, but the run fails until it is replaced"
# A copy is named to the second. The run just above can finish within the second this
# one starts in, and its copy would then be replaced under the same name instead of
# joined by a new one (CI on main, 2026-11-24): start in the next second.
sleep 1
copies=$(rclone lsf dest:visacrm-backup/db/daily/ | wc -l)
status=0
run --env-file "$work/owner.env" -e BACKUP_MONTHLY=auto "$IMAGE" 2> "$work/old-key.log" || status=$?
cat "$work/old-key.log" >&2
[ "$status" = 78 ] || fail "the run with the old key ended with $status, not 78"
grep -q 'BACKUP_S3_ACCESS_KEY' "$work/old-key.log" || fail "the failure does not name the old key"
[ "$(rclone lsf dest:visacrm-backup/db/daily/ | wc -l)" = $((copies + 1)) ] ||
  fail "the run with the old key made no copy"
echo "OK"

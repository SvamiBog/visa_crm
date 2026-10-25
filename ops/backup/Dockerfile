# Backup job (backlog 0.11, docs/backup.md): a Railway cron service next to the web
# service, built from this directory. pg_dump must be of the production server's major
# version or newer, so PG_MAJOR follows it (SHOW server_version on the Railway database).
ARG PG_MAJOR=18
FROM postgres:${PG_MAJOR}-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends age rclone curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY remotes.sh backup.sh restore.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/restore.sh

# Not root: the job only reads the database and talks to the buckets.
USER postgres
ENTRYPOINT ["backup.sh"]

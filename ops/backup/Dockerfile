# Backup job (backlog 0.11, docs/backup.md): a Railway cron service next to the web
# service, built from this directory. pg_dump must be of the production server's major
# version or newer, so PG_MAJOR follows it (SHOW server_version on the Railway database).
ARG PG_MAJOR=18

# rclone from its official image, pinned by digest: backup.sh locks the monthly copies
# with --s3-object-lock-*, which rclone has since 1.74 (Debian bookworm ships 1.60).
# The binary is static, so it runs in the Debian image below.
FROM rclone/rclone:1.74.0@sha256:d2e0e88359d0b2e67cfcd2c43d5405185eb8adfc207079df27c42da82c5207bc AS rclone

FROM postgres:${PG_MAJOR}-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends age curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
COPY remotes.sh backup.sh restore.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/restore.sh

# Not root: the job only reads the database and talks to the buckets.
USER postgres
ENTRYPOINT ["backup.sh"]

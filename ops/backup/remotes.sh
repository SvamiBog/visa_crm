# rclone remotes of the backup job, configured from the environment; sourced by
# backup.sh and restore.sh. "dest" is the external backup bucket (BACKUP_S3_*), "files"
# the app's files bucket (the S3_* variables the web service uses). The keys may be
# limited to one bucket, so rclone does not try to check or create buckets.
export RCLONE_CONFIG_DEST_TYPE=s3
export RCLONE_CONFIG_DEST_PROVIDER=Other
export RCLONE_CONFIG_DEST_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
export RCLONE_CONFIG_DEST_REGION="${BACKUP_S3_REGION:-}"
export RCLONE_CONFIG_DEST_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY:-}"
export RCLONE_CONFIG_DEST_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY:-}"
export RCLONE_CONFIG_DEST_NO_CHECK_BUCKET=true

export RCLONE_CONFIG_FILES_TYPE=s3
export RCLONE_CONFIG_FILES_PROVIDER=Other
export RCLONE_CONFIG_FILES_ENDPOINT="${S3_ENDPOINT:-}"
export RCLONE_CONFIG_FILES_REGION="${S3_REGION:-}"
export RCLONE_CONFIG_FILES_ACCESS_KEY_ID="${S3_ACCESS_KEY:-}"
export RCLONE_CONFIG_FILES_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-}"
export RCLONE_CONFIG_FILES_NO_CHECK_BUCKET=true

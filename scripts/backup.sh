#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

source "$PROJECT_DIR/.env.backup"
source "$PROJECT_DIR/.env" 2>/dev/null || true

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="videos-${TIMESTAMP}.db.gz"
LOCAL_PATH="${BACKUP_DIR}/${FILENAME}"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: DB not found at $DB_PATH"
    exit 1
fi

gzip -c "$DB_PATH" > "$LOCAL_PATH"
echo "Backup created: $LOCAL_PATH ($(du -h "$LOCAL_PATH" | cut -f1))"

if [ -n "${R2_BUCKET:-}" ] && command -v rclone &>/dev/null; then
    rclone copy "$LOCAL_PATH" "${R2_REMOTE:-r2}:${R2_BUCKET}/" --progress --s3-no-check-bucket
    echo "Uploaded to R2: ${R2_BUCKET}/${FILENAME}"

    MAX_BYTES=$((8 * 1024 * 1024 * 1024))
    if command -v python3 &>/dev/null; then
        python3 - "
import json, subprocess, sys
try:
    max_bytes = int(sys.argv[1])
    data = json.loads(subprocess.check_output(['rclone', 'lsjson', '--files-only', sys.argv[2]]))
    total = sum(f['Size'] for f in data)
    if total > max_bytes:
        files = sorted(data, key=lambda f: f['ModTime'])
        freed = 0
        for f in files:
            if total <= max_bytes:
                break
            subprocess.run(['rclone', 'delete', f'{sys.argv[2]}/{f[\"Name\"]}'], check=True, capture_output=True)
            total -= f['Size']
            freed += 1
        print(f'R2 cleanup: deleted {freed} oldest backup(s) to stay under 8GB')
except Exception as e:
    print(f'R2 cleanup check skipped: {e}')
" "$MAX_BYTES" "${R2_REMOTE:-r2}:${R2_BUCKET}"
    fi
else
    echo "WARNING: R2 not configured or rclone not installed. Backup stored locally only."
fi

find "$BACKUP_DIR" -name "videos-*.db.gz" -mtime +"$RETENTION_DAYS" -delete
echo "Cleaned up local backups older than $RETENTION_DAYS days"

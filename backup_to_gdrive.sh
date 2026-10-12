#!/usr/bin/env bash
# ==============================================================================
# Automated Google Drive Backup Script for GPON Network Mapping
# Uses SQLite Online Backup + Tar Compression + Rclone
# ==============================================================================

set -euo pipefail

# Dynamic Directory Discovery
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/gpon_survey_data.db" ] || [ -f "$SCRIPT_DIR/server.py" ]; then
    DEFAULT_APP_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../gpon_survey_data.db" ] || [ -f "$SCRIPT_DIR/../server.py" ]; then
    DEFAULT_APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
    DEFAULT_APP_DIR="${HOME}/Network-Mapping"
fi

APP_DIR="${PROJECT_DIR:-$DEFAULT_APP_DIR}"
DB_FILE="$APP_DIR/gpon_survey_data.db"
USERS_CONFIG="$APP_DIR/users_config.json"
HIERARCHY_CONFIG="$APP_DIR/custom_hierarchy.json"

BACKUP_LOCAL_DIR="${BACKUP_DIR:-$APP_DIR/gpon_backups}"
LOG_FILE="$APP_DIR/backup.log"
STATUS_FILE="$APP_DIR/backup_status.json"
RCLONE_REMOTE="${RCLONE_DEST:-gdrive:GPON_Backups}"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
TEMP_SNAPSHOT="$BACKUP_LOCAL_DIR/snapshot_$TIMESTAMP.db"
ARCHIVE_NAME="gpon_backup_$TIMESTAMP.tar.gz"
ARCHIVE_PATH="$BACKUP_LOCAL_DIR/$ARCHIVE_NAME"

mkdir -p "$BACKUP_LOCAL_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Python executable detection
if command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="python"
else
    PY_BIN=""
fi

write_status() {
    local status="$1"
    local uploaded="$2"
    local size="${3:-0B}"
    local archive="${4:-}"
    local msg="${5:-}"
    msg="${msg//\"/\\\"}"
    local iso_now
    iso_now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local human_now
    human_now=$(date +"%d %b %Y, %I:%M:%S %p")

    cat <<EOF > "$STATUS_FILE"
{
  "status": "$status",
  "uploaded_to_cloud": $uploaded,
  "archive_size": "$size",
  "archive_name": "$archive",
  "remote": "$RCLONE_REMOTE",
  "timestamp": "$iso_now",
  "readable_time": "$human_now",
  "message": "$msg"
}
EOF
}

handle_error() {
    local exit_code=$?
    log "ERROR: Backup job failed at line $1 with exit code $exit_code."
    write_status "failed" false "0B" "${ARCHIVE_NAME:-}" "Backup failed at line $1 with exit code $exit_code. Check backup.log."
    exit "$exit_code"
}

trap 'handle_error $LINENO' ERR

log "=== Starting Scheduled Backup ==="
write_status "in_progress" false "0B" "$ARCHIVE_NAME" "Backup snapshot and compression in progress..."

# 1. Hot SQLite Backup (Safe online snapshot with zero corruption & zero downtime)
if [ -f "$DB_FILE" ]; then
    log "Creating safe SQLite live snapshot..."
    if [ -z "$PY_BIN" ]; then
        log "ERROR: Python is required for safe live SQLite backup snapshot."
        exit 1
    fi
    $PY_BIN -c "
import sqlite3
src = sqlite3.connect('''$DB_FILE''', timeout=30.0)
dst = sqlite3.connect('''$TEMP_SNAPSHOT''')
src.backup(dst)
dst.close()
src.close()
"
else
    log "WARNING: Database file $DB_FILE not found!"
    write_status "failed" false "0B" "$ARCHIVE_NAME" "Database file $DB_FILE not found!"
    exit 1
fi

# 2. Package database, user configuration, and node hierarchy into compressed tarball
log "Compressing backup archive: $ARCHIVE_NAME..."
TAR_FILES=("-C" "$BACKUP_LOCAL_DIR" "snapshot_$TIMESTAMP.db")

# Add users_config.json if it exists
if [ -f "$USERS_CONFIG" ]; then
    cp "$USERS_CONFIG" "$BACKUP_LOCAL_DIR/users_config.json"
    TAR_FILES+=("-C" "$BACKUP_LOCAL_DIR" "users_config.json")
fi

# Add custom_hierarchy.json if it exists
if [ -f "$HIERARCHY_CONFIG" ]; then
    cp "$HIERARCHY_CONFIG" "$BACKUP_LOCAL_DIR/custom_hierarchy.json"
    TAR_FILES+=("-C" "$BACKUP_LOCAL_DIR" "custom_hierarchy.json")
fi

tar -czf "$ARCHIVE_PATH" "${TAR_FILES[@]}"

# Remove temporary uncompressed snapshot
rm -f "$TEMP_SNAPSHOT"
rm -f "$BACKUP_LOCAL_DIR/users_config.json" 2>/dev/null || true
rm -f "$BACKUP_LOCAL_DIR/custom_hierarchy.json" 2>/dev/null || true

ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
log "Archive created successfully ($ARCHIVE_SIZE)."

# 3. Upload to Google Drive using Rclone
if command -v rclone >/dev/null 2>&1; then
    log "Uploading $ARCHIVE_NAME to Google Drive ($RCLONE_REMOTE)..."
    if rclone copy "$ARCHIVE_PATH" "$RCLONE_REMOTE"; then
        log "Upload completed successfully!"

        # 4. Retention policy: clean up Google Drive backups older than 30 days
        log "Pruning remote backups older than 30 days..."
        rclone delete --min-age 30d "$RCLONE_REMOTE" || true

        write_status "success" true "$ARCHIVE_SIZE" "$ARCHIVE_NAME" "Backup created and synced to Google Drive successfully."
    else
        log "ERROR: rclone upload to Google Drive failed."
        write_status "failed" false "$ARCHIVE_SIZE" "$ARCHIVE_NAME" "Local backup created, but Google Drive sync failed. Check rclone credentials or network."
    fi
else
    log "WARNING: rclone not installed or configured. Backup preserved locally at $ARCHIVE_PATH."
    write_status "warning" false "$ARCHIVE_SIZE" "$ARCHIVE_NAME" "Local backup created ($ARCHIVE_SIZE), but rclone is not installed or configured."
fi

# 5. Local cleanup: keep only last 7 days of local archives
find "$BACKUP_LOCAL_DIR" -type f -name "gpon_backup_*.tar.gz" -mtime +7 -delete 2>/dev/null || true

log "=== Backup Finished Successfully ==="

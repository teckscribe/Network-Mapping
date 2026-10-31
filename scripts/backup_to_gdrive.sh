#!/usr/bin/env bash
# ==============================================================================
# Automated Google Cloud & Drive Backup Script for GPON Network Mapping
# Uses SQLite Online Backup + Tar Compression + Google Account Sync / Rclone
# ==============================================================================

set -uo pipefail

# Configuration
APP_DIR="${PROJECT_DIR:-$HOME/Network-Mapping}"
DB_FILE="$APP_DIR/gpon_survey_data.db"
USERS_CONFIG="$APP_DIR/users_config.json"
HIERARCHY_CONFIG_MAIN="$APP_DIR/Node Master/custom_hierarchy.json"
HIERARCHY_CONFIG_ALT="$APP_DIR/custom_hierarchy.json"

BACKUP_LOCAL_DIR="${BACKUP_DIR:-$HOME/gpon_backups}"
LOG_FILE="$APP_DIR/backup.log"
RCLONE_REMOTE="gdrive:GPON_Backups"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
TEMP_SNAPSHOT="$BACKUP_LOCAL_DIR/snapshot_$TIMESTAMP.db"
ARCHIVE_NAME="gpon_backup_$TIMESTAMP.tar.gz"
ARCHIVE_PATH="$BACKUP_LOCAL_DIR/$ARCHIVE_NAME"

mkdir -p "$BACKUP_LOCAL_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=== Starting Scheduled Backup ==="

# 1. Hot SQLite Backup (Safe online snapshot with zero corruption & zero downtime)
if [ -f "$DB_FILE" ]; then
    log "Creating safe SQLite live snapshot..."
    python3 -c "
import sqlite3
src = sqlite3.connect('$DB_FILE', timeout=30.0)
dst = sqlite3.connect('$TEMP_SNAPSHOT')
src.backup(dst)
dst.close()
src.close()
"
else
    log "WARNING: Database file $DB_FILE not found!"
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
if [ -f "$HIERARCHY_CONFIG_MAIN" ]; then
    cp "$HIERARCHY_CONFIG_MAIN" "$BACKUP_LOCAL_DIR/custom_hierarchy.json"
    TAR_FILES+=("-C" "$BACKUP_LOCAL_DIR" "custom_hierarchy.json")
elif [ -f "$HIERARCHY_CONFIG_ALT" ]; then
    cp "$HIERARCHY_CONFIG_ALT" "$BACKUP_LOCAL_DIR/custom_hierarchy.json"
    TAR_FILES+=("-C" "$BACKUP_LOCAL_DIR" "custom_hierarchy.json")
fi

tar -czf "$ARCHIVE_PATH" "${TAR_FILES[@]}"

# Remove temporary uncompressed snapshot
rm -f "$TEMP_SNAPSHOT"
rm -f "$BACKUP_LOCAL_DIR/users_config.json" 2>/dev/null || true
rm -f "$BACKUP_LOCAL_DIR/custom_hierarchy.json" 2>/dev/null || true

ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
log "Archive created successfully ($ARCHIVE_SIZE)."

# 3. Upload to Google Drive using Rclone (non-blocking if not configured)
if command -v rclone >/dev/null 2>&1; then
    if rclone listremotes 2>/dev/null | grep -q "^gdrive:"; then
        log "Uploading $ARCHIVE_NAME to Google Drive ($RCLONE_REMOTE)..."
        if rclone copy "$ARCHIVE_PATH" "$RCLONE_REMOTE" --contimeout 5s --timeout 30s; then
            log "Rclone upload completed successfully!"
            rclone delete --min-age 30d "$RCLONE_REMOTE" 2>/dev/null || true
        else
            log "WARNING: Rclone upload failed or token expired. Falling back to Google Account Cloud Sync..."
        fi
    fi
fi

# 4. Local cleanup: keep only last 7 days of local archives
find "$BACKUP_LOCAL_DIR" -type f -name "gpon_backup_*.tar.gz" -mtime +7 -delete 2>/dev/null || true

# 5. Upload to Google Account Cloud (Email + App Password configured in Web UI) & update backup_status.json
if [ -f "$APP_DIR/scripts/verify_backup_health.py" ]; then
    python3 "$APP_DIR/scripts/verify_backup_health.py" --cloud-sync --update 2>/dev/null || true
fi

log "=== Backup Finished Successfully ==="

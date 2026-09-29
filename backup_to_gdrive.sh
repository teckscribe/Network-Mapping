#!/usr/bin/env bash
# ==============================================================================
# Automated Google Drive Backup Script for GPON Network Mapping
# Uses SQLite Online Backup + Tar Compression + Rclone
# ==============================================================================

set -euo pipefail

# Configuration
APP_DIR="${PROJECT_DIR:-$HOME/Network-Mapping}"
DB_FILE="$APP_DIR/gpon_survey_data.db"
USERS_CONFIG="$APP_DIR/users_config.json"
HIERARCHY_CONFIG="$APP_DIR/custom_hierarchy.json"

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
    rclone copy "$ARCHIVE_PATH" "$RCLONE_REMOTE"
    log "Upload completed successfully!"

    # 4. Retention policy: clean up Google Drive backups older than 30 days
    log "Pruning remote backups older than 30 days..."
    rclone delete --min-age 30d "$RCLONE_REMOTE" || true
else
    log "WARNING: rclone not installed or configured. Backup preserved locally at $ARCHIVE_PATH."
fi

# 5. Local cleanup: keep only last 7 days of local archives
find "$BACKUP_LOCAL_DIR" -type f -name "gpon_backup_*.tar.gz" -mtime +7 -delete 2>/dev/null || true

log "=== Backup Finished Successfully ==="

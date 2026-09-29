#!/usr/bin/env bash
# ==============================================================================
# GPON Network Mapping - Automated Google Drive Backup Script
# Safe online backup of live SQLite database with WAL consistency + Gzip compression
# ==============================================================================

set -euo pipefail

# Configuration
PROJECT_DIR="/home/psms/Network-Mapping"
DB_PATH="${PROJECT_DIR}/gpon_survey_data.db"
USERS_JSON="${PROJECT_DIR}/users_config.json"
BACKUP_DIR="/home/psms/backups"
RCLONE_REMOTE="gdrive:GPON_Backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DB="${BACKUP_DIR}/gpon_db_${TIMESTAMP}.db"
LOG_FILE="${BACKUP_DIR}/backup.log"

# Ensure local backup directory exists
mkdir -p "${BACKUP_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"
}

log "=== Starting GPON Backup ==="

# 1. Verify source database exists
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: Database file ${DB_PATH} not found!"
    exit 1
fi

# 2. Perform atomic, live-safe SQLite online backup (handles WAL mode cleanly)
log "Performing online SQLite backup to ${BACKUP_DB}..."
python3 -c "
import sqlite3
src = sqlite3.connect('${DB_PATH}', timeout=30.0)
dst = sqlite3.connect('${BACKUP_DB}')
src.backup(dst)
dst.close()
src.close()
"

# 3. Also backup users_config.json if it exists
if [ -f "${USERS_JSON}" ]; then
    cp "${USERS_JSON}" "${BACKUP_DIR}/users_config_${TIMESTAMP}.json"
fi

# 4. Compress the backup with gzip
log "Compressing backup file..."
gzip -f "${BACKUP_DB}"
COMPRESSED_FILE="${BACKUP_DB}.gz"
log "Backup archive created: ${COMPRESSED_FILE} ($(du -h "${COMPRESSED_FILE}" | cut -f1))"

# 5. Check if rclone is installed and configured
if command -v rclone &> /dev/null; then
    if rclone listremotes | grep -q "^gdrive:"; then
        log "Uploading backup to Google Drive (${RCLONE_REMOTE})..."
        rclone copy "${COMPRESSED_FILE}" "${RCLONE_REMOTE}/" --log-file="${LOG_FILE}" --log-level NOTICE
        if [ -f "${BACKUP_DIR}/users_config_${TIMESTAMP}.json" ]; then
            rclone copy "${BACKUP_DIR}/users_config_${TIMESTAMP}.json" "${RCLONE_REMOTE}/"
        fi
        log "Google Drive upload completed successfully!"

        # Prune remote backups older than 30 days
        log "Pruning Google Drive backups older than 30 days..."
        rclone delete --min-age 30d "${RCLONE_REMOTE}/" || true
    else
        log "WARNING: rclone remote 'gdrive:' not configured. Run 'rclone config' to link Google Drive."
    fi
else
    log "WARNING: rclone is not installed. Run 'sudo apt install rclone' to enable Google Drive sync."
fi

# 6. Local Retention Policy: Keep only the last 7 days of local backups
log "Cleaning local backups older than 7 days..."
find "${BACKUP_DIR}" -name "gpon_db_*.db.gz" -mtime +7 -delete
find "${BACKUP_DIR}" -name "users_config_*.json" -mtime +7 -delete

log "=== Backup Finished Successfully ==="

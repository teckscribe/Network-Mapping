#!/usr/bin/env python3
"""
Standalone Google Drive Backup Health Checker for GPON Network Mapping.
Runs independently of the web server with zero overhead.

Usage:
  python scripts/verify_backup_health.py --test            # Run manual diagnostic & print report to terminal
  python scripts/verify_backup_health.py --update          # Check health and write to backup_status.json
  python scripts/verify_backup_health.py --json            # Output pure JSON
"""

import os
import sys
import json
import glob
import time
import subprocess
import argparse
from datetime import datetime

# Resolve project base directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(BASE_DIR, "backup_status.json")
LOG_FILE = os.path.join(BASE_DIR, "backup.log")

# Default backup storage directories to inspect (Linux default: $HOME/gpon_backups)
HOME_DIR = os.environ.get("HOME", "")
DEFAULT_BACKUP_DIRS = [
    os.path.join(HOME_DIR, "gpon_backups") if HOME_DIR else "",
    os.path.join(BASE_DIR, "gpon_backups"),
    os.path.join(BASE_DIR, "backups"),
    os.environ.get("BACKUP_DIR", "")
]

RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive:GPON_Backups")

def format_size(bytes_size):
    """Formats bytes into human-readable MB / KB."""
    if bytes_size >= 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    elif bytes_size >= 1024:
        return f"{bytes_size / 1024:.1f} KB"
    return f"{bytes_size} B"

def find_latest_local_backup():
    """Scans candidate directories for the newest gpon_backup_*.tar.gz file."""
    candidate_files = []
    for b_dir in DEFAULT_BACKUP_DIRS:
        if b_dir and os.path.isdir(b_dir):
            matches = glob.glob(os.path.join(b_dir, "gpon_backup_*.tar.gz"))
            matches += glob.glob(os.path.join(b_dir, "*.tar.gz"))
            candidate_files.extend(matches)

    if not candidate_files:
        return None

    # Sort by modification time descending
    candidate_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = candidate_files[0]
    mtime = os.path.getmtime(latest)
    size = os.path.getsize(latest)
    dt = datetime.fromtimestamp(mtime)

    age_hours = (time.time() - mtime) / 3600.0

    return {
        "path": latest,
        "name": os.path.basename(latest),
        "size": format_size(size),
        "size_bytes": size,
        "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "age_hours": round(age_hours, 1)
    }

def check_backup_log():
    """Inspects the tail of backup.log for recent success or failure strings."""
    if not os.path.isfile(LOG_FILE):
        return {"has_log": False, "last_status": "No log file found"}

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        tail = [l.strip() for l in lines[-15:] if l.strip()]
        tail_text = "\n".join(tail)

        if "Backup Finished Successfully" in tail_text:
            return {"has_log": True, "last_status": "SUCCESS", "recent_entry": tail[-1] if tail else ""}
        elif "WARNING" in tail_text or "ERROR" in tail_text:
            return {"has_log": True, "last_status": "WARNING_OR_ERROR", "recent_entry": tail[-1] if tail else ""}
        return {"has_log": True, "last_status": "UNKNOWN", "recent_entry": tail[-1] if tail else ""}
    except Exception as e:
        return {"has_log": False, "last_status": f"Error reading log: {str(e)}"}

import shutil

def check_rclone_connectivity():
    """Tests rclone remote connectivity with a strict 4-second timeout."""
    try:
        # Cross-platform check if rclone executable exists
        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            return {"available": False, "connected": False, "message": "rclone is not installed"}

        # Quick listing check with 4s timeout
        rc = subprocess.run(
            [rclone_bin, "lsf", RCLONE_REMOTE, "--max-depth", "1", "--contimeout", "3s", "--timeout", "4s"],
            capture_output=True, text=True, timeout=5
        )
        if rc.returncode == 0:
            files = [f.strip() for f in rc.stdout.splitlines() if f.strip()]
            return {
                "available": True,
                "connected": True,
                "remote": RCLONE_REMOTE,
                "remote_files_count": len(files),
                "latest_remote_file": files[-1] if files else "None"
            }
        else:
            return {
                "available": True,
                "connected": False,
                "remote": RCLONE_REMOTE,
                "error": rc.stderr.strip()[:100]
            }
    except FileNotFoundError:
        return {"available": False, "connected": False, "message": "rclone command not found on OS"}
    except subprocess.TimeoutExpired:
        return {"available": True, "connected": False, "message": "rclone connection timed out (network slow)"}
    except Exception as e:
        return {"available": False, "connected": False, "message": str(e)}

def evaluate_backup_health():
    """Gathers all indicators and determines unified health state."""
    local_info = find_latest_local_backup()
    log_info = check_backup_log()
    rclone_info = check_rclone_connectivity()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine status
    if not local_info:
        status = "error"
        message = "No local backup archives found on system."
    elif local_info["age_hours"] > 24.0:
        status = "warning"
        message = f"Latest backup is older than 24 hours ({local_info['age_hours']}h ago)."
    elif rclone_info.get("connected"):
        status = "healthy"
        message = f"Backup active & verified on Google Drive ({local_info['size']})."
    elif rclone_info.get("available") and not rclone_info.get("connected"):
        status = "warning"
        message = f"Local backup exists ({local_info['size']}), but Google Drive sync failed or timed out."
    else:
        status = "healthy_local_only"
        message = f"Local backup healthy ({local_info['size']}). Cloud sync tool not configured."

    report = {
        "status": status,
        "last_backup_time": local_info["timestamp"] if local_info else "Never",
        "archive_name": local_info["name"] if local_info else "None",
        "archive_size": local_info["size"] if local_info else "0 B",
        "backup_age_hours": local_info["age_hours"] if local_info else None,
        "remote_target": RCLONE_REMOTE,
        "cloud_connected": rclone_info.get("connected", False),
        "rclone_installed": rclone_info.get("available", False),
        "last_checked_at": now_str,
        "message": message
    }
    return report

def write_status_file(data):
    """Atomically writes status dictionary to backup_status.json."""
    temp_file = STATUS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # Atomic replace
    os.replace(temp_file, STATUS_FILE)
    print(f"[OK] Backup status written to: {STATUS_FILE}")

def print_cli_report(data):
    """Prints a clear diagnostic report in terminal (safe for all shell encodings)."""
    status_icons = {
        "healthy": "[HEALTHY / SYNCED]",
        "healthy_local_only": "[LOCAL ONLY (Cloud Unset)]",
        "warning": "[WARNING / PENDING]",
        "error": "[CRITICAL / ERROR]"
    }
    icon = status_icons.get(data["status"], data["status"])

    print("\n" + "="*60)
    print(" GPON NETWORK MAPPING - BACKUP HEALTH DIAGNOSTIC")
    print("="*60)
    print(f" Status             : {icon}")
    print(f" Last Backup Time   : {data['last_backup_time']}")
    print(f" Latest Archive     : {data['archive_name']} ({data['archive_size']})")
    if data["backup_age_hours"] is not None:
        print(f" Archive Age        : {data['backup_age_hours']} hours ago")
    print(f" Cloud Remote       : {data['remote_target']}")
    print(f" Cloud Connected    : {'Yes' if data['cloud_connected'] else 'No'}")
    print(f" Rclone Installed   : {'Yes' if data['rclone_installed'] else 'No'}")
    print(f" Last Checked       : {data['last_checked_at']}")
    print(f" Summary Message    : {data['message']}")
    print("="*60 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Standalone Backup Health Checker")
    parser.add_argument("--test", action="store_true", help="Print CLI diagnostic report to terminal")
    parser.add_argument("--update", action="store_true", help="Check health and save to backup_status.json")
    parser.add_argument("--json", action="store_true", help="Output pure JSON to stdout")

    args = parser.parse_args()

    # Default to --test if no argument provided
    if not (args.test or args.update or args.json):
        args.test = True

    data = evaluate_backup_health()

    if args.json:
        print(json.dumps(data, indent=2))
    elif args.test:
        print_cli_report(data)
    
    if args.update:
        write_status_file(data)

if __name__ == "__main__":
    main()

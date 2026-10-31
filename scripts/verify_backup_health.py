#!/usr/bin/env python3
"""
Standalone Google Cloud & Drive Backup Engine + Health Checker for GPON Network Mapping.
Supports two cloud backup replication channels:
  1. Google Account Cloud Backup (Username / Email + 16-Digit Google App Password via TLS)
  2. Rclone Google Drive Remote (gdrive:GPON_Backups)

Usage:
  python scripts/verify_backup_health.py --test            # Run manual diagnostic & print report to terminal
  python scripts/verify_backup_health.py --update          # Check health and write to backup_status.json
  python scripts/verify_backup_health.py --json            # Output pure JSON
  python scripts/verify_backup_health.py --run-backup      # Create hot SQLite .tar.gz snapshot + upload to Google Cloud
  python scripts/verify_backup_health.py --cloud-sync      # Upload latest local .tar.gz archive to Google Account & update status
"""

import os
import sys
import json
import glob
import time
import socket
import shutil
import sqlite3
import tarfile
import smtplib
import subprocess
import argparse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# Resolve project base directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(BASE_DIR, "backup_status.json")
LOG_FILE = os.path.join(BASE_DIR, "backup.log")
ENV_FILE = os.path.join(BASE_DIR, ".env")
DB_FILE = os.path.join(BASE_DIR, "gpon_survey_data.db")
USERS_CONFIG_FILE = os.path.join(BASE_DIR, "users_config.json")
HIERARCHY_FILE_MAIN = os.path.join(BASE_DIR, "Node Master", "custom_hierarchy.json")
HIERARCHY_FILE_ALT = os.path.join(BASE_DIR, "custom_hierarchy.json")

# Default backup storage directories to inspect (Linux default: $HOME/gpon_backups)
HOME_DIR = os.environ.get("HOME", "") or os.path.expanduser("~")
DEFAULT_BACKUP_DIRS = [
    os.environ.get("BACKUP_DIR", ""),
    os.path.join(HOME_DIR, "gpon_backups") if HOME_DIR else "",
    os.path.join(BASE_DIR, "gpon_backups"),
    os.path.join(BASE_DIR, "backups")
]

RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive:GPON_Backups")


def log_backup_event(msg: str):
    """Appends a timestamped entry to backup.log."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def mask_email(email_str: str) -> str:
    """Masks an email address for safe UI display (e.g., a***p@gmail.com)."""
    if not email_str or "@" not in email_str:
        return email_str or ""
    user_part, domain_part = email_str.split("@", 1)
    if len(user_part) <= 2:
        masked_user = user_part[0] + "*"
    else:
        masked_user = user_part[0] + ("*" * (len(user_part) - 2)) + user_part[-1]
    return f"{masked_user}@{domain_part}"


def load_backup_config() -> dict:
    """Dynamically loads Google Cloud Backup settings from .env or environment variables."""
    env_vars = {}
    if os.path.isfile(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as ef:
                for line in ef:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass

    def get_val(key, default=""):
        return env_vars.get(key) or os.environ.get(key, default)

    # Fallback to SMTP_USER / SMTP_PASSWORD if dedicated BACKUP_GOOGLE_* is not explicitly set
    smtp_user = get_val("SMTP_USER", "")
    smtp_pass = get_val("SMTP_PASSWORD", "")
    smtp_host = get_val("SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com"
    try:
        smtp_port = int(get_val("SMTP_PORT", "587") or "587")
    except ValueError:
        smtp_port = 587

    google_user = get_val("BACKUP_GOOGLE_USER", "") or smtp_user
    google_pass = get_val("BACKUP_GOOGLE_PASSWORD", "")
    if not google_pass and google_user and google_user == smtp_user:
        google_pass = smtp_pass

    # Strip spaces from 16-digit Google App Password if formatted as "xxxx xxxx xxxx xxxx"
    google_pass_clean = google_pass.replace(" ", "") if google_pass else ""

    recipient_email = get_val("BACKUP_RECIPIENT_EMAIL", "") or google_user
    server_label = get_val("BACKUP_SERVER_LABEL", "") or socket.gethostname() or "GPON-Server"
    backup_host = get_val("BACKUP_SMTP_HOST", "") or smtp_host
    try:
        backup_port = int(get_val("BACKUP_SMTP_PORT", "") or str(smtp_port))
    except ValueError:
        backup_port = 587

    return {
        "google_user": google_user.strip(),
        "google_password": google_pass_clean,
        "recipient_email": recipient_email.strip(),
        "server_label": server_label.strip(),
        "smtp_host": backup_host.strip(),
        "smtp_port": backup_port,
        "configured": bool(google_user.strip() and google_pass_clean)
    }


def save_backup_config_to_env(
    google_user: str,
    google_password: str = "",
    recipient_email: str = "",
    server_label: str = "",
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587
):
    """Safely updates or appends BACKUP_* (and empty SMTP_*) keys in .env without losing existing settings."""
    current = load_backup_config()
    final_user = google_user.strip()
    final_pass = google_password.replace(" ", "").strip() if google_password else current["google_password"]
    final_recipient = (recipient_email or "").strip() or final_user
    final_label = (server_label or "").strip() or current["server_label"]
    final_host = (smtp_host or "smtp.gmail.com").strip()
    final_port = int(smtp_port or 587)

    updates = {
        "BACKUP_GOOGLE_USER": final_user,
        "BACKUP_GOOGLE_PASSWORD": final_pass,
        "BACKUP_RECIPIENT_EMAIL": final_recipient,
        "BACKUP_SERVER_LABEL": final_label,
        "BACKUP_SMTP_HOST": final_host,
        "BACKUP_SMTP_PORT": str(final_port),
    }

    lines = []
    existing_keys = set()
    existing_kv = {}
    if os.path.isfile(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for l in lines:
                s = l.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    existing_kv[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            lines = []

    # Also auto-populate SMTP_* if SMTP_USER was not configured yet (so OTP emails work seamlessly too)
    if not existing_kv.get("SMTP_USER") and final_user and final_pass:
        updates["SMTP_HOST"] = final_host
        updates["SMTP_PORT"] = str(final_port)
        updates["SMTP_USER"] = final_user
        updates["SMTP_PASSWORD"] = final_pass
        updates["SMTP_FROM"] = final_user
        updates["SMTP_TLS"] = "true"

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                existing_keys.add(k)
            else:
                new_lines.append(line if line.endswith("\n") else line + "\n")
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    for k, v in updates.items():
        if k not in existing_keys:
            new_lines.append(f"{k}={v}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def format_size(bytes_size):
    """Formats bytes into human-readable MB / KB."""
    if bytes_size >= 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    elif bytes_size >= 1024:
        return f"{bytes_size / 1024:.1f} KB"
    return f"{bytes_size} B"


def get_primary_backup_dir() -> str:
    """Returns the preferred writable directory for local backup archives."""
    for b_dir in DEFAULT_BACKUP_DIRS:
        if b_dir and os.path.isdir(b_dir):
            return b_dir
    target = os.path.join(HOME_DIR, "gpon_backups") if HOME_DIR else os.path.join(BASE_DIR, "gpon_backups")
    os.makedirs(target, exist_ok=True)
    return target


def find_latest_local_backup():
    """Scans candidate directories for the newest gpon_backup_*.tar.gz file."""
    candidate_files = []
    seen = set()
    for b_dir in DEFAULT_BACKUP_DIRS:
        if b_dir and os.path.isdir(b_dir):
            matches = glob.glob(os.path.join(b_dir, "gpon_backup_*.tar.gz"))
            matches += glob.glob(os.path.join(b_dir, "*.tar.gz"))
            for m in matches:
                abs_m = os.path.abspath(m)
                if abs_m not in seen and os.path.isfile(abs_m):
                    seen.add(abs_m)
                    candidate_files.append(abs_m)

    if not candidate_files:
        return None

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
        return {"has_log": False, "last_status": "No log file found", "recent_lines": []}

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        tail = [l.strip() for l in lines[-20:] if l.strip()]
        tail_text = "\n".join(tail)

        if "Backup Finished Successfully" in tail_text or "Google Cloud backup email dispatched" in tail_text:
            return {"has_log": True, "last_status": "SUCCESS", "recent_entry": tail[-1] if tail else "", "recent_lines": tail}
        elif "WARNING" in tail_text or "ERROR" in tail_text:
            return {"has_log": True, "last_status": "WARNING_OR_ERROR", "recent_entry": tail[-1] if tail else "", "recent_lines": tail}
        return {"has_log": True, "last_status": "UNKNOWN", "recent_entry": tail[-1] if tail else "", "recent_lines": tail}
    except Exception as e:
        return {"has_log": False, "last_status": f"Error reading log: {str(e)}", "recent_lines": []}


def check_google_account_connectivity(verify_login: bool = True) -> dict:
    """Checks if Google Account (Email + App Password) is configured and optionally verifies SMTP TLS login."""
    conf = load_backup_config()
    if not conf["configured"]:
        return {
            "available": False,
            "connected": False,
            "account": "",
            "message": "Google Account username & App Password not configured yet."
        }

    masked = mask_email(conf["google_user"])
    if not verify_login:
        return {
            "available": True,
            "connected": True,
            "account": masked,
            "recipient": conf["recipient_email"],
            "message": f"Configured for Google Account ({masked})"
        }

    try:
        server = smtplib.SMTP(conf["smtp_host"], conf["smtp_port"], timeout=6)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(conf["google_user"], conf["google_password"])
        server.quit()
        return {
            "available": True,
            "connected": True,
            "account": masked,
            "recipient": conf["recipient_email"],
            "remote": f"Google Cloud ({masked})",
            "message": f"Authenticated with Google Account ({masked})"
        }
    except smtplib.SMTPAuthenticationError as e:
        err_msg = "Google rejected login. Please use a 16-digit Google App Password (myaccount.google.com/apppasswords)."
        return {
            "available": True,
            "connected": False,
            "account": masked,
            "error": f"{err_msg} [{e.smtp_code}]"
        }
    except Exception as e:
        return {
            "available": True,
            "connected": False,
            "account": masked,
            "error": f"Google connection error: {str(e)[:120]}"
        }


def check_rclone_connectivity() -> dict:
    """Tests rclone remote connectivity with a strict 4-second timeout."""
    try:
        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            return {"available": False, "connected": False, "message": "rclone is not installed"}

        # First check if remote is even configured in rclone.conf
        rem_proc = subprocess.run(
            [rclone_bin, "listremotes"],
            capture_output=True, text=True, timeout=3
        )
        remotes = [r.strip() for r in rem_proc.stdout.splitlines() if r.strip()]
        remote_prefix = RCLONE_REMOTE.split(":")[0] + ":"
        if remote_prefix not in remotes:
            return {
                "available": True,
                "connected": False,
                "remote": RCLONE_REMOTE,
                "configured": False,
                "error": f"Remote '{remote_prefix}' is not configured in rclone.conf on this server."
            }

        rc = subprocess.run(
            [rclone_bin, "lsf", RCLONE_REMOTE, "--max-depth", "1", "--contimeout", "3s", "--timeout", "4s"],
            capture_output=True, text=True, timeout=5
        )
        if rc.returncode == 0:
            files = [f.strip() for f in rc.stdout.splitlines() if f.strip()]
            return {
                "available": True,
                "connected": True,
                "configured": True,
                "remote": RCLONE_REMOTE,
                "remote_files_count": len(files),
                "latest_remote_file": files[-1] if files else "None"
            }
        else:
            err_raw = rc.stderr.strip()
            if "token" in err_raw.lower() or "expired" in err_raw.lower() or "oauth" in err_raw.lower():
                friendly_err = "Rclone Google OAuth token expired. Configure Google Account App Password in Admin UI for permanent sync."
            else:
                friendly_err = err_raw[:140]
            return {
                "available": True,
                "connected": False,
                "configured": True,
                "remote": RCLONE_REMOTE,
                "error": friendly_err
            }
    except FileNotFoundError:
        return {"available": False, "connected": False, "message": "rclone command not found on OS"}
    except subprocess.TimeoutExpired:
        return {"available": True, "connected": False, "message": "rclone connection timed out"}
    except Exception as e:
        return {"available": False, "connected": False, "message": str(e)}


def create_hot_backup_archive() -> dict:
    """
    Creates a zero-downtime hot SQLite backup snapshot and packages it with
    users_config.json and custom_hierarchy.json into a timestamped .tar.gz archive.
    """
    backup_dir = get_primary_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    temp_db = os.path.join(backup_dir, f"snapshot_{ts}.db")
    archive_name = f"gpon_backup_{ts}.tar.gz"
    archive_path = os.path.join(backup_dir, archive_name)

    log_backup_event("=== Starting Hot Backup Snapshot ===")

    if not os.path.isfile(DB_FILE):
        msg = f"ERROR: Database file {DB_FILE} not found!"
        log_backup_event(msg)
        raise FileNotFoundError(msg)

    # 1. Safe Online SQLite Backup
    src = sqlite3.connect(DB_FILE, timeout=30.0)
    dst = sqlite3.connect(temp_db)
    src.backup(dst)
    dst.close()
    src.close()

    # 2. Create compressed .tar.gz bundle
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(temp_db, arcname=f"snapshot_{ts}.db")
        if os.path.isfile(USERS_CONFIG_FILE):
            tar.add(USERS_CONFIG_FILE, arcname="users_config.json")
        if os.path.isfile(HIERARCHY_FILE_MAIN):
            tar.add(HIERARCHY_FILE_MAIN, arcname="custom_hierarchy.json")
        elif os.path.isfile(HIERARCHY_FILE_ALT):
            tar.add(HIERARCHY_FILE_ALT, arcname="custom_hierarchy.json")

    try:
        os.remove(temp_db)
    except Exception:
        pass

    # 3. Prune local backups older than 7 days
    cutoff = time.time() - (7 * 86400)
    for old_f in glob.glob(os.path.join(backup_dir, "gpon_backup_*.tar.gz")):
        try:
            if os.path.getmtime(old_f) < cutoff and os.path.abspath(old_f) != os.path.abspath(archive_path):
                os.remove(old_f)
        except Exception:
            pass

    size_bytes = os.path.getsize(archive_path)
    size_str = format_size(size_bytes)
    log_backup_event(f"Local archive created: {archive_name} ({size_str})")

    return {
        "path": archive_path,
        "name": archive_name,
        "size": size_str,
        "size_bytes": size_bytes,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def upload_archive_to_google_account(archive_path: str) -> dict:
    """
    Uploads the compressed .tar.gz backup archive to the configured Google Account
    via authenticated TLS (smtp.gmail.com:587) using Google Email + App Password.
    """
    conf = load_backup_config()
    if not conf["configured"]:
        return {"uploaded": False, "message": "Google Account credentials not configured."}

    if not os.path.isfile(archive_path):
        return {"uploaded": False, "message": f"Archive file not found: {archive_path}"}

    archive_name = os.path.basename(archive_path)
    size_str = format_size(os.path.getsize(archive_path))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    server_label = conf["server_label"]

    # Gather quick database counts for the backup manifest
    rec_count = 0
    user_count = 0
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM survey_records")
        rec_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass

    msg = MIMEMultipart()
    msg["Subject"] = f"[GPON Cloud Backup] {server_label} - {now_str} ({size_str})"
    msg["From"] = f"GPON Cloud Backup ({server_label}) <{conf['google_user']}>"
    msg["To"] = conf["recipient_email"]

    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f8fafc; padding:20px;">
  <div style="max-width:560px; margin:0 auto; background:#ffffff; border-radius:12px; border:1px solid #cbd5e1; padding:24px;">
    <h2 style="margin:0 0 12px 0; color:#0284c7;">☁️ Automated GPON Cloud Backup</h2>
    <p style="color:#475569; font-size:14px; line-height:1.5;">
      Attached is the compressed hot-snapshot backup archive containing the SQLite survey database, user accounts configuration, and optical node hierarchy.
    </p>
    <table style="width:100%; border-collapse:collapse; margin:16px 0; font-size:13px;">
      <tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:8px 0; color:#64748b;"><strong>Server / Zone:</strong></td><td style="padding:8px 0; font-weight:700; color:#0f172a;">{server_label}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:8px 0; color:#64748b;"><strong>Backup Timestamp:</strong></td><td style="padding:8px 0; color:#0f172a;">{now_str}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:8px 0; color:#64748b;"><strong>Archive File:</strong></td><td style="padding:8px 0; font-family:monospace; color:#0284c7;">{archive_name}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:8px 0; color:#64748b;"><strong>Compressed Size:</strong></td><td style="padding:8px 0; font-weight:700; color:#10b981;">{size_str}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0;"><td style="padding:8px 0; color:#64748b;"><strong>Survey Records Included:</strong></td><td style="padding:8px 0; font-weight:700;">{rec_count} records</td></tr>
      <tr><td style="padding:8px 0; color:#64748b;"><strong>Registered Users Included:</strong></td><td style="padding:8px 0; font-weight:700;">{user_count} users</td></tr>
    </table>
    <p style="font-size:12px; color:#94a3b8; margin-top:16px;">
      Tip: You can click "Add to Drive" on the attachment in Gmail to pin this archive directly into your Google Drive folder.
    </p>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(html_body, "html"))

    with open(archive_path, "rb") as af:
        part = MIMEApplication(af.read(), Name=archive_name)
    part["Content-Disposition"] = f'attachment; filename="{archive_name}"'
    msg.attach(part)

    try:
        server = smtplib.SMTP(conf["smtp_host"], conf["smtp_port"], timeout=25)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(conf["google_user"], conf["google_password"])
        recipients = [r.strip() for r in conf["recipient_email"].split(",") if r.strip()]
        server.sendmail(conf["google_user"], recipients, msg.as_string())
        server.quit()
        masked = mask_email(conf["google_user"])
        log_backup_event(f"Google Cloud backup email dispatched successfully to {conf['recipient_email']} ({size_str}).")
        return {
            "uploaded": True,
            "account": masked,
            "recipient": conf["recipient_email"],
            "message": f"Uploaded {archive_name} ({size_str}) to Google Account ({masked})"
        }
    except Exception as e:
        err_str = str(e)
        log_backup_event(f"ERROR uploading backup to Google Account: {err_str}")
        return {
            "uploaded": False,
            "error": err_str
        }


def upload_archive_via_rclone(archive_path: str) -> dict:
    """Attempts to upload archive via rclone if rclone and remote are configured."""
    rclone_bin = shutil.which("rclone")
    if not rclone_bin or not os.path.isfile(archive_path):
        return {"uploaded": False, "message": "rclone not available"}
    try:
        rc = subprocess.run(
            [rclone_bin, "copy", archive_path, RCLONE_REMOTE, "--contimeout", "5s", "--timeout", "30s"],
            capture_output=True, text=True, timeout=35
        )
        if rc.returncode == 0:
            log_backup_event(f"Rclone upload completed to {RCLONE_REMOTE}.")
            return {"uploaded": True, "remote": RCLONE_REMOTE}
        else:
            return {"uploaded": False, "error": rc.stderr.strip()[:140]}
    except Exception as e:
        return {"uploaded": False, "error": str(e)}


def evaluate_backup_health():
    """Gathers all indicators (local snapshot, Google Account Cloud, and Rclone) and determines unified health state."""
    local_info = find_latest_local_backup()
    log_info = check_backup_log()
    google_info = check_google_account_connectivity(verify_login=True)
    rclone_info = check_rclone_connectivity()
    conf = load_backup_config()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cloud_connected = bool(google_info.get("connected") or rclone_info.get("connected"))

    # Determine active remote target label
    if google_info.get("connected") and rclone_info.get("connected"):
        remote_target = f"Google Account ({google_info['account']}) + {RCLONE_REMOTE}"
        auth_method = "Google Account + Rclone"
    elif google_info.get("connected"):
        remote_target = f"Google Cloud ({google_info['account']})"
        auth_method = "Google Account (Username & App Password)"
    elif rclone_info.get("connected"):
        remote_target = RCLONE_REMOTE
        auth_method = "Rclone (Google Drive)"
    elif conf.get("configured"):
        remote_target = f"Google Cloud ({mask_email(conf['google_user'])})"
        auth_method = "Google Account (Login Failed)"
    else:
        remote_target = RCLONE_REMOTE
        auth_method = "Not Configured"

    # Determine status & diagnostic message
    if not local_info:
        status = "error"
        message = "No local backup archives found on system. Click 'Run Backup Now' to create your first backup."
    elif local_info["age_hours"] > 24.0:
        status = "warning"
        message = f"Latest backup is older than 24 hours ({local_info['age_hours']}h ago). Click 'Run Backup Now'."
    elif cloud_connected:
        status = "healthy"
        message = f"Backup active & verified on {remote_target} ({local_info['size']})."
    else:
        status = "warning"
        reasons = []
        if google_info.get("error"):
            reasons.append(google_info["error"])
        elif rclone_info.get("error"):
            reasons.append(rclone_info["error"])
        else:
            reasons.append("Enter your Google Email & 16-digit App Password below to enable automatic Google Cloud sync.")
        message = f"Local backup saved ({local_info['size']}). Cloud sync disconnected: {' '.join(reasons)}"

    report = {
        "status": status,
        "last_backup_time": local_info["timestamp"] if local_info else "Never",
        "archive_name": local_info["name"] if local_info else "None",
        "archive_size": local_info["size"] if local_info else "0 B",
        "backup_age_hours": local_info["age_hours"] if local_info else None,
        "remote_target": remote_target,
        "auth_method": auth_method,
        "cloud_connected": cloud_connected,
        "google_account_connected": google_info.get("connected", False),
        "google_account_masked": mask_email(conf.get("google_user", "")),
        "rclone_installed": rclone_info.get("available", False),
        "rclone_connected": rclone_info.get("connected", False),
        "rclone_error": rclone_info.get("error", ""),
        "google_error": google_info.get("error", ""),
        "server_label": conf.get("server_label", ""),
        "last_checked_at": now_str,
        "message": message,
        "recent_logs": log_info.get("recent_lines", [])[-10:]
    }
    return report


def run_full_backup_workflow() -> dict:
    """Executes hot local snapshot, uploads to Google Account and/or Rclone, and updates backup_status.json."""
    archive_info = create_hot_backup_archive()
    archive_path = archive_info["path"]

    google_res = upload_archive_to_google_account(archive_path)
    rclone_res = upload_archive_via_rclone(archive_path)

    if google_res.get("uploaded") or rclone_res.get("uploaded"):
        log_backup_event("=== Backup Finished Successfully ===")
    else:
        log_backup_event("WARNING: Local archive created, but no cloud channel succeeded.")

    health = evaluate_backup_health()
    write_status_file(health)
    return {
        "archive": archive_info,
        "google_upload": google_res,
        "rclone_upload": rclone_res,
        "health": health
    }


def write_status_file(data):
    """Atomically writes status dictionary to backup_status.json."""
    temp_file = STATUS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
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
    print(f" Auth Method        : {data.get('auth_method', 'N/A')}")
    print(f" Cloud Connected    : {'Yes' if data['cloud_connected'] else 'No'}")
    print(f" Rclone Installed   : {'Yes' if data['rclone_installed'] else 'No'}")
    print(f" Last Checked       : {data['last_checked_at']}")
    print(f" Summary Message    : {data['message']}")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Standalone Backup Engine & Health Checker")
    parser.add_argument("--test", action="store_true", help="Print CLI diagnostic report to terminal")
    parser.add_argument("--update", action="store_true", help="Check health and save to backup_status.json")
    parser.add_argument("--json", action="store_true", help="Output pure JSON to stdout")
    parser.add_argument("--run-backup", action="store_true", help="Create hot backup archive and sync to cloud")
    parser.add_argument("--cloud-sync", action="store_true", help="Upload latest local archive to Google Account")

    args = parser.parse_args()

    if args.run_backup:
        res = run_full_backup_workflow()
        if args.json:
            print(json.dumps(res["health"], indent=2))
        else:
            print_cli_report(res["health"])
        return

    if args.cloud_sync:
        latest = find_latest_local_backup()
        if latest and latest.get("path"):
            upload_archive_to_google_account(latest["path"])

    if not (args.test or args.update or args.json or args.cloud_sync):
        args.test = True

    data = evaluate_backup_health()

    if args.json:
        print(json.dumps(data, indent=2))
    elif args.test:
        print_cli_report(data)

    if args.update or args.cloud_sync:
        write_status_file(data)


if __name__ == "__main__":
    main()

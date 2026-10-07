"""
GPON Field Survey Central Server
FastAPI + SQLite backend with Offline-First Sync, User Authentication, Center Filtering, and Central Office Dashboard.
"""

import os
import sqlite3
import datetime
import time
import random
import secrets
import hashlib
import hmac
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import socket
import io
import json
import re
import uuid
import zipfile
import shutil
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Response, Depends, Header, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Union

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_APP_DIR = os.path.join(BASE_DIR, "web_app")
DB_PATH = os.path.join(BASE_DIR, "gpon_survey_data.db")
USERS_CONFIG_PATH = os.path.join(BASE_DIR, "users_config.json")
NODE_MASTER_DIR = os.path.join(BASE_DIR, "Node Master")
DATA_DIR = os.path.join(BASE_DIR, "data")
HIERARCHY_FILE = os.path.join(NODE_MASTER_DIR, "custom_hierarchy.json")

# ==========================================
# SMTP EMAIL CONFIGURATION (.env supported)
# ==========================================
ENV_FILE = os.path.join(BASE_DIR, ".env")

def load_smtp_config():
    """Dynamically read SMTP settings from .env file or environment variables."""
    conf = {
        "host": os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_email": os.getenv("SMTP_FROM", ""),
        "from_name": os.getenv("SMTP_FROM_NAME", "GPON Network Mapping"),
        "tls": os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes")
    }
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as ef:
                for line in ef:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k == "SMTP_HOST": conf["host"] = v
                        elif k == "SMTP_PORT":
                            try: conf["port"] = int(v)
                            except: conf["port"] = 587
                        elif k == "SMTP_USER": conf["user"] = v
                        elif k == "SMTP_PASSWORD": conf["password"] = v
                        elif k == "SMTP_FROM": conf["from_email"] = v
                        elif k == "SMTP_FROM_NAME": conf["from_name"] = v
                        elif k == "SMTP_TLS": conf["tls"] = v.lower() in ("true", "1", "yes")
        except Exception as e:
            print(f"[SMTP Config] Warning reading .env: {e}")
    if not conf["from_email"]:
        conf["from_email"] = conf["user"] or "noreply@gpon.local"
    return conf

_init_smtp = load_smtp_config()
SMTP_HOST = _init_smtp["host"]
SMTP_PORT = _init_smtp["port"]
SMTP_USER = _init_smtp["user"]
SMTP_PASSWORD = _init_smtp["password"]
SMTP_FROM = _init_smtp["from_email"]
SMTP_FROM_NAME = _init_smtp["from_name"]
SMTP_TLS = _init_smtp["tls"]

# In-Memory OTP Store: { username: { "otp": "123456", "expires_at": float, "attempts": int, "email": "...", "full_name": "..." } }
PASSWORD_RESET_OTPS = {}
OTP_EXPIRY_SECONDS = 600  # 10 minutes

# ==========================================
# SECURE PASSWORD HASHING (PBKDF2-SHA256)
# ==========================================
# Format: "pbkdf2$<iterations>$<hex_salt>$<hex_hash>"
# Zero external dependencies — uses Python built-in hashlib.
HASH_ITERATIONS = 260000  # OWASP 2023 recommended minimum for PBKDF2-SHA256

def hash_password(plaintext: str) -> str:
    """Hash a plaintext password using PBKDF2-SHA256 with random salt."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', plaintext.encode('utf-8'), salt, HASH_ITERATIONS)
    return f"pbkdf2${HASH_ITERATIONS}${salt.hex()}${dk.hex()}"

def verify_password(plaintext: str, stored: str) -> bool:
    """Verify a plaintext password against a stored hash. Also accepts legacy plaintext match."""
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        # Hashed password
        try:
            _, iters_str, salt_hex, hash_hex = stored.split("$", 3)
            iters = int(iters_str)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            dk = hashlib.pbkdf2_hmac('sha256', plaintext.encode('utf-8'), salt, iters)
            return secrets.compare_digest(dk, expected)
        except (ValueError, TypeError):
            return False
    else:
        # Legacy plaintext comparison (for migration period)
        return plaintext == stored

def is_hashed(password_str: str) -> bool:
    """Check if a password string is already hashed."""
    return bool(password_str and password_str.startswith("pbkdf2$"))

# ==========================================
# STATELESS HMAC-SHA256 SESSION TOKENS
# ==========================================
# Cryptographically signed tokens that survive server restarts.
# Zero external dependencies — uses Python built-in hmac, hashlib, base64.
SECRET_KEY_FILE = os.path.join(BASE_DIR, ".secret_key")

def get_secret_key() -> bytes:
    key_env = os.getenv("SECRET_KEY")
    if key_env:
        return key_env.encode('utf-8')
    if os.path.exists(SECRET_KEY_FILE):
        try:
            with open(SECRET_KEY_FILE, "rb") as f:
                k = f.read().strip()
                if k:
                    return k
        except Exception:
            pass
    new_key = secrets.token_bytes(32)
    try:
        with open(SECRET_KEY_FILE, "wb") as f:
            f.write(new_key)
    except Exception:
        pass
    return new_key

SESSION_EXPIRY_SECONDS = 86400 * 7  # 7-day token validity

def create_session_token(username: str, role: str) -> str:
    """Create an HMAC-SHA256 signed session token."""
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + SESSION_EXPIRY_SECONDS
    }
    payload_json = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode('utf-8').rstrip('=')
    sig = hmac.new(get_secret_key(), payload_b64.encode('utf-8'), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode('utf-8').rstrip('=')
    return f"{payload_b64}.{sig_b64}"

def validate_session_token(token: str) -> Optional[dict]:
    """Validate an HMAC-SHA256 signed session token. Returns dict or None."""
    if not token or "." not in token:
        return None
    try:
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        payload_b64, sig_b64 = parts

        # Verify HMAC signature in constant time
        expected_sig = hmac.new(get_secret_key(), payload_b64.encode('utf-8'), hashlib.sha256).digest()
        pad_sig = 4 - (len(sig_b64) % 4)
        sig_b64_padded = sig_b64 + ("=" * pad_sig) if pad_sig != 4 else sig_b64
        actual_sig = base64.urlsafe_b64decode(sig_b64_padded.encode('utf-8'))
        if not secrets.compare_digest(expected_sig, actual_sig):
            return None

        # Decode payload
        pad_p = 4 - (len(payload_b64) % 4)
        payload_b64_padded = payload_b64 + ("=" * pad_p) if pad_p != 4 else payload_b64
        payload = json.loads(base64.urlsafe_b64decode(payload_b64_padded.encode('utf-8')).decode('utf-8'))

        if time.time() > payload.get("exp", 0):
            return None

        return {"username": payload.get("sub", ""), "role": payload.get("role", "field_technician")}
    except Exception:
        return None

def get_current_session(authorization: Optional[str] = Header(None), token: Optional[str] = Query(None)) -> Optional[dict]:
    """FastAPI dependency: extracts and validates Bearer token from Authorization header or ?token= query parameter."""
    token_str = None
    if authorization:
        token_str = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization.strip()
    elif token:
        token_str = token.strip()
    if not token_str:
        return None
    return validate_session_token(token_str)

def require_admin_auth(session: Optional[dict] = Depends(get_current_session)) -> dict:
    """FastAPI dependency: requires a valid session with super_admin role."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    if session.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin privileges required for this action.")
    return session

def require_management_auth(session: Optional[dict] = Depends(get_current_session)) -> dict:
    """FastAPI dependency: requires super_admin or rcsm role."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    if session.get("role") not in ("super_admin", "rcsm"):
        raise HTTPException(status_code=403, detail="Management access (Super Admin or RCSM) required.")
    return session

def require_export_auth(session: Optional[dict] = Depends(get_current_session)) -> dict:
    """FastAPI dependency: requires valid authenticated session for center exports."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    if session.get("role") not in ("super_admin", "admin", "supervisor", "rcsm", "acso", "field_technician"):
        raise HTTPException(status_code=403, detail="Permission Denied: Invalid role for export.")
    return session

def require_any_auth(session: Optional[dict] = Depends(get_current_session)) -> dict:
    """FastAPI dependency: requires any valid authenticated session."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    return session

# ==========================================
# CLIENT IP & RATE LIMITING HELPERS
# ==========================================
LOGIN_ATTEMPTS = {}
OTP_REQUEST_ATTEMPTS = {}

def get_client_ip(request: Request) -> str:
    """Extract real client IP address even when behind reverse proxies (Nginx, Cloudflare, Tailscale)."""
    x_forwarded = request.headers.get("x-forwarded-for")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    x_real = request.headers.get("x-real-ip")
    if x_real:
        return x_real.strip()
    return request.client.host if request.client else "unknown"

def check_rate_limit(store: dict, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Returns True if within rate limit, False if exceeded. Evicts expired keys to prevent memory leak."""
    now = time.time()
    if len(store) > 1000:
        keys_to_del = [k for k, timestamps in store.items() if not timestamps or (now - timestamps[-1] >= window_seconds)]
        for k in keys_to_del:
            store.pop(k, None)

    timestamps = [t for t in store.get(key, []) if now - t < window_seconds]
    if len(timestamps) >= max_attempts:
        store[key] = timestamps
        return False
    timestamps.append(now)
    store[key] = timestamps
    return True


VALID_ROLES = {
    "super_admin": "Super Admin",
    "rcsm": "RCSM",
    "acso": "ACSO",
    "field_technician": "Field Technician"
}

def normalize_role(role: str) -> str:
    r = (role or "").strip().lower()
    if r in ("admin", "superadmin", "super_admin"):
        return "super_admin"
    if r in ("rcsm", "regional_manager", "manager"):
        return "rcsm"
    if r in ("acso", "officer"):
        return "acso"
    if r in ("field_agent", "field_technician", "technician", "agent", "user"):
        return "field_technician"
    return "field_technician"

BANNED_SAMPLE_USERS = {"rcsm_thrissur", "acso_thrissur", "thrissur_agent", "tmm_agent"}


def save_users_to_json(conn=None):
    """Persists current SQLite users to users_config.json so git pulls never wipe user credentials."""
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_at_end = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, password, full_name, assigned_center, assigned_region, role, created_at, email FROM users WHERE username NOT IN ('rcsm_thrissur', 'acso_thrissur', 'thrissur_agent', 'tmm_agent')")
        rows = cur.fetchall()
        users_list = []
        for r in rows:
            users_list.append({
                "username": r[0],
                "password": r[1],
                "full_name": r[2],
                "assigned_center": r[3],
                "assigned_region": r[4] or "Thrissur",
                "role": normalize_role(r[5]),
                "created_at": r[6],
                "email": (r[7] or "").strip() if len(r) > 7 and r[7] else ""
            })
        with open(USERS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(users_list, f, indent=2)
        print(f"[Config] Saved {len(users_list)} users to persistent {USERS_CONFIG_PATH}")
    except Exception as e:
        print(f"[Config Warning] Could not save users to JSON backup: {e}")
    finally:
        if close_at_end:
            conn.close()

def load_users_from_json(conn):
    """Restores user credentials from users_config.json into SQLite."""
    if not os.path.exists(USERS_CONFIG_PATH):
        return False
    try:
        with open(USERS_CONFIG_PATH, "r", encoding="utf-8") as f:
            users_list = json.load(f)
        if not users_list or not isinstance(users_list, list):
            return False
        cur = conn.cursor()
        now = datetime.datetime.now().isoformat()
        loaded_count = 0
        for u in users_list:
            uname = u.get("username", "").strip()
            if not uname or uname in BANNED_SAMPLE_USERS:
                continue
            cur.execute("""
            INSERT INTO users (username, password, full_name, email, assigned_center, assigned_region, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password=excluded.password,
                full_name=excluded.full_name,
                email=excluded.email,
                assigned_center=excluded.assigned_center,
                assigned_region=excluded.assigned_region,
                role=excluded.role
            """, (
                uname,
                u["password"].strip(),
                u["full_name"].strip(),
                u.get("email", "").strip(),
                u["assigned_center"].strip(),
                u.get("assigned_region", "Thrissur"),
                normalize_role(u.get("role", "field_technician")),
                u.get("created_at", now)
            ))
            loaded_count += 1
        conn.commit()
        print(f"[Config] Restored and verified {loaded_count} users from persistent {USERS_CONFIG_PATH}")
        return True
    except Exception as e:
        print(f"[Config Warning] Could not restore users from JSON backup: {e}")
        return False

# Init SQLite Database
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA busy_timeout = 10000;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    
    # 1. Survey Records Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS survey_records (
        client_uuid TEXT PRIMARY KEY,
        region TEXT,
        center TEXT,
        rt_room TEXT,
        technology TEXT,
        olt_name TEXT,
        port_number TEXT,
        kseb_post_number TEXT,
        landmark TEXT,
        enclosure_number TEXT,
        enclosure_id TEXT,
        lat_long TEXT,
        splitter_id TEXT,
        splitter_ratio TEXT,
        customers_connected INTEGER,
        splitter_lead_color TEXT,
        adl_subscriber_id TEXT,
        acs_subscriber_id TEXT,
        survey_date_time TEXT,
        device_id TEXT,
        surveyor_username TEXT,
        surveyor_name TEXT,
        created_at TEXT,
        synced_at TEXT
    )
    """)

    # 2. Users Table for Field Authentication & Center Assignment
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        full_name TEXT NOT NULL,
        email TEXT DEFAULT '',
        assigned_center TEXT NOT NULL,
        assigned_region TEXT DEFAULT 'Thrissur',
        role TEXT DEFAULT 'field_technician',
        created_at TEXT
    )
    """)

    # 3. Password Reset OTP Table (survives multi-worker Uvicorn and server restarts)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_otps (
        username TEXT PRIMARY KEY,
        otp TEXT NOT NULL,
        expires_at REAL NOT NULL,
        attempts INTEGER DEFAULT 0,
        email TEXT DEFAULT '',
        full_name TEXT DEFAULT ''
    )
    """)

    # Check and add columns if upgrading existing db
    cur.execute("PRAGMA table_info(users)")
    user_cols = [c[1] for c in cur.fetchall()]
    if "email" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
    if "assigned_region" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN assigned_region TEXT DEFAULT 'Thrissur'")
    if "role" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'field_technician'")
    if "created_at" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
    cur.execute("UPDATE users SET email = '' WHERE email IS NULL")
    cur.execute("UPDATE users SET assigned_region = 'Thrissur' WHERE assigned_region IS NULL OR assigned_region = ''")
    cur.execute("UPDATE users SET role = 'field_technician' WHERE role IS NULL OR role = ''")
    conn.commit()

    cur.execute("PRAGMA table_info(survey_records)")
    cols = [c[1] for c in cur.fetchall()]
    if "surveyor_username" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN surveyor_username TEXT")
    if "surveyor_name" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN surveyor_name TEXT")
    if "splitter_lead_color" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN splitter_lead_color TEXT")
    if "adl_subscriber_id" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN adl_subscriber_id TEXT")
    if "acs_subscriber_id" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN acs_subscriber_id TEXT")
    if "survey_date_time" not in cols:
        cur.execute("ALTER TABLE survey_records ADD COLUMN survey_date_time TEXT")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_records_enc_spl ON survey_records(enclosure_id, splitter_id)")

    # 1. Sync from persistent users_config.json if it exists
    load_users_from_json(conn)

    # 2. Purge sample demo users if present
    sample_users = ('rcsm_thrissur', 'acso_thrissur', 'thrissur_agent', 'tmm_agent')
    cur.execute(f"DELETE FROM users WHERE username IN ({','.join(['?']*len(sample_users))})", sample_users)

    # 3. Seed Root Super Admin User if missing
    cur.execute("SELECT username FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        now = datetime.datetime.now().isoformat()
        cur.execute("""
        INSERT INTO users (username, password, full_name, email, assigned_center, assigned_region, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "Central Super Administrator", "admin@gpon.local", "ALL", "ALL", "super_admin", now))

    # Normalize existing legacy roles in SQLite table
    cur.execute("UPDATE users SET role = 'super_admin' WHERE role = 'admin'")
    cur.execute("UPDATE users SET role = 'field_technician' WHERE role = 'field_agent'")

    # 4. Auto-upgrade all unhashed passwords in SQLite to secure PBKDF2-SHA256
    cur.execute("SELECT username, password FROM users")
    upgraded_count = 0
    for u_name, u_pwd in cur.fetchall():
        if u_pwd and not is_hashed(u_pwd):
            cur.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(u_pwd.strip()), u_name))
            upgraded_count += 1
    if upgraded_count > 0:
        print(f"[Security] Auto-upgraded {upgraded_count} user password(s) to PBKDF2-SHA256 hashes.")

    # Normalize existing user centers against uploaded Node Master Excel
    hier_data = load_hierarchy_data()
    if hier_data:
        center_lookup = {c.strip().upper(): c for c in hier_data.keys()}
        center_lookup["THATHAMANGALM"] = "THATHAMANGALAM"
        cur.execute("SELECT username, assigned_center FROM users")
        for u_name, u_center in cur.fetchall():
            if u_center and u_center != "ALL":
                c_upper = u_center.strip().upper()
                if c_upper in center_lookup:
                    canonical = center_lookup[c_upper]
                    if canonical != u_center:
                        cur.execute("UPDATE users SET assigned_center = ? WHERE username = ?", (canonical, u_name))
    conn.commit()

    # 3. Always ensure users_config.json is up-to-date with current database users
    save_users_to_json(conn)
    conn.close()

def sanitize_folder_name(name: str, default: str = "Unknown") -> str:
    """Sanitizes strings for safe folder and file names across Windows and Linux."""
    if not name or not str(name).strip():
        return default
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', str(name).strip())
    cleaned = cleaned.strip('. ')
    return cleaned if cleaned else default

def prune_hierarchy(hier: dict) -> dict:
    """Removes empty RT rooms ({}) and empty centers ({}) to eliminate ghost counts and dangling nodes."""
    if not isinstance(hier, dict):
        return {}
    clean = {}
    for c, rts in hier.items():
        c_clean = str(c).strip()
        if not c_clean or not isinstance(rts, dict) or not rts:
            continue
        clean_rts = {}
        for rt, olts in rts.items():
            rt_clean = str(rt).strip()
            if not rt_clean or not isinstance(olts, dict) or not olts:
                continue
            clean_olts = {}
            for olt_name, olt_data in olts.items():
                olt_clean = str(olt_name).strip()
                if olt_clean and olt_data:
                    clean_olts[olt_clean] = olt_data
            if clean_olts:
                clean_rts[rt_clean] = clean_olts
        if clean_rts:
            clean[c_clean] = clean_rts
    return clean

def load_hierarchy_data() -> dict:
    """Loads network hierarchy from HIERARCHY_FILE (or fallback to legacy custom_hierarchy.json) and prunes ghost entries."""
    if os.path.exists(HIERARCHY_FILE):
        try:
            with open(HIERARCHY_FILE, "r", encoding="utf-8") as f:
                return prune_hierarchy(json.load(f))
        except Exception as e:
            print(f"[Hierarchy Warning] Failed loading {HIERARCHY_FILE}: {e}")
    legacy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                return prune_hierarchy(json.load(f))
        except Exception:
            pass
    return {}

def save_hierarchy_data(data: dict):
    """Saves sanitized network hierarchy to HIERARCHY_FILE and mirrors to legacy path for backward compatibility."""
    clean_data = prune_hierarchy(data)
    os.makedirs(NODE_MASTER_DIR, exist_ok=True)
    with open(HIERARCHY_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, indent=2)
    try:
        legacy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)
    except Exception:
        pass

def get_region_for_center(center_name: str, hierarchy: dict = None) -> str:
    """Finds the assigned Region for a given Center from the hierarchy dictionary."""
    if not center_name:
        return "Thrissur"
    if hierarchy is None:
        hierarchy = load_hierarchy_data()
    c_lower = str(center_name).strip().lower()
    for c, rts in hierarchy.items():
        if c.strip().lower() == c_lower and isinstance(rts, dict):
            for rt, olts in rts.items():
                if isinstance(olts, dict):
                    for olt_k, olt_data in olts.items():
                        if isinstance(olt_data, dict) and olt_data.get("region"):
                            return olt_data["region"].strip()
    return "Thrissur"

def get_user_jurisdiction(username: str) -> dict:
    """Returns jurisdiction dictionary for a given username:
    {
        'role': str,
        'is_super_admin': bool,
        'regions': list[str] (lowercased),
        'is_all_regions': bool,
        'centers': list[str] (lowercased),
        'is_all_centers': bool
    }
    """
    if not username:
        return {
            'role': 'field_technician',
            'is_super_admin': False,
            'regions': [],
            'is_all_regions': False,
            'centers': [],
            'is_all_centers': False
        }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT role, assigned_region, assigned_center FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))", (username.strip(),))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {
            'role': 'field_technician',
            'is_super_admin': False,
            'regions': [],
            'is_all_regions': False,
            'centers': [],
            'is_all_centers': False
        }

    role = normalize_role(row["role"])
    if role == "super_admin":
        return {
            'role': 'super_admin',
            'is_super_admin': True,
            'regions': [],
            'is_all_regions': True,
            'centers': [],
            'is_all_centers': True
        }

    raw_reg = (row["assigned_region"] or "").strip()
    raw_cent = (row["assigned_center"] or "").strip()

    regs = [r.strip().lower() for r in raw_reg.split(",") if r.strip()]
    is_all_reg = ("all" in regs) or (not regs)

    cents = [c.strip().lower() for c in raw_cent.split(",") if c.strip()]
    is_all_cent = ("all" in cents) or (not cents)

    return {
        'role': role,
        'is_super_admin': False,
        'regions': regs,
        'is_all_regions': is_all_reg,
        'centers': cents,
        'is_all_centers': is_all_cent
    }

def build_excel_workbook(rows, title="Survey_Data") -> openpyxl.Workbook:
    """Builds and styles an openpyxl Workbook for GPON survey records."""
    headers = [
        'Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', 'OLT/Node Name',
        'Port Number', 'KSEB Post Number', 'Land Mark', 'Enclosure Number',
        'Enclosure ID', 'Lat /Long', 'Splitter ID', 'Splitter Ratio',
        'No: Of Customer Connected', 'Splitter Out Colour Code',
        'ADL Subscriber ID', 'ACS Subscriber ID', 'Date & Time', 'Surveyor Name'
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31] if title else "Sheet1"

    header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="000000")
    thin_border = Border(
        left=Side(style="thin", color="BFBFBF"),
        right=Side(style="thin", color="BFBFBF"),
        top=Side(style="thin", color="BFBFBF"),
        bottom=Side(style="thin", color="BFBFBF")
    )
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    for row_idx, r in enumerate(rows, 3):
        vals = [
            r['region'] if r['region'] else 'Thrissur',
            r['center'] or '',
            r['rt_room'] or '',
            r['technology'] or 'GPON',
            r['olt_name'] or '',
            r['port_number'] or '',
            r['kseb_post_number'] or '',
            r['landmark'] or '',
            r['enclosure_number'] or '',
            r['enclosure_id'] or '',
            r['lat_long'] or '',
            r['splitter_id'] or '',
            r['splitter_ratio'] or '',
            r['customers_connected'] or 0,
            r['splitter_lead_color'] or '',
            r['adl_subscriber_id'] or '',
            r['acs_subscriber_id'] or '',
            r['survey_date_time'] or (r['created_at'][:19].replace('T', ' ') if r['created_at'] else ''),
            r['surveyor_name'] or r['surveyor_username'] or ''
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            if col_idx in [1, 2, 3, 4, 6, 9, 12, 13, 14, 15, 18]:
                cell.alignment = center_align
            else:
                cell.alignment = left_align

    for col in ws.columns:
        max_len = 0
        col_letter = col[1].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    return wb

def workbook_to_bytes(wb: openpyxl.Workbook) -> bytes:
    """Serializes an openpyxl Workbook into an in-memory byte buffer (zero disk writes)."""
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()

def ensure_directories_and_migrate():
    """Initializes 'Node Master' directory and cleans up static disk .xlsx files."""
    os.makedirs(NODE_MASTER_DIR, exist_ok=True)

    # 1. Migrate custom_hierarchy.json into Node Master/
    legacy_hier = os.path.join(BASE_DIR, "custom_hierarchy.json")
    if os.path.exists(legacy_hier):
        if not os.path.exists(HIERARCHY_FILE):
            try:
                shutil.copy2(legacy_hier, HIERARCHY_FILE)
                print(f"[Init] Migrated {legacy_hier} -> {HIERARCHY_FILE}")
            except Exception as e:
                print(f"[Init Warning] Could not copy hierarchy file: {e}")
        else:
            if os.path.getsize(HIERARCHY_FILE) == 0 and os.path.getsize(legacy_hier) > 0:
                try:
                    shutil.copy2(legacy_hier, HIERARCHY_FILE)
                except Exception:
                    pass

    # 2. Clean up static 'data/' folder from disk (all Excel generation is now streamed dynamically in-memory)
    data_folder = os.path.join(BASE_DIR, "data")
    if os.path.exists(data_folder):
        try:
            shutil.rmtree(data_folder, ignore_errors=True)
            print("[Init] Cleaned up static data/ folder (all Excel files now stream in-memory).")
        except Exception as e:
            print(f"[Init Warning] Could not clean up data/ folder: {e}")

    # 3. Clean up temporary static .bak export files from BASE_DIR and Node Master
    for dir_to_clean in [BASE_DIR, NODE_MASTER_DIR]:
        if os.path.exists(dir_to_clean):
            for fname in os.listdir(dir_to_clean):
                if fname.endswith(".bak") or fname.startswith("temp_"):
                    try:
                        os.remove(os.path.join(dir_to_clean, fname))
                    except Exception:
                        pass

    # 4. Prune legacy ghost RT rooms / empty centers from hierarchy files
    try:
        current_hier = load_hierarchy_data()
        save_hierarchy_data(current_hier)
    except Exception as e:
        print(f"[Init Warning] Could not sanitize hierarchy file: {e}")

init_db()
ensure_directories_and_migrate()

app = FastAPI(title="GPON Field Survey Server")

# Allow CORS for mobile web requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models
class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreateModel(BaseModel):
    username: str
    password: Optional[str] = ""
    full_name: str
    email: Optional[str] = ""
    assigned_center: Union[str, List[str]]
    assigned_region: Optional[Union[str, List[str]]] = "Thrissur"
    role: Optional[str] = "field_technician"

class ChangePasswordRequest(BaseModel):
    username: str
    email: str
    new_password: str

class RequestResetOtpPayload(BaseModel):
    username_or_email: str

class VerifyResetOtpPayload(BaseModel):
    username: str
    otp: str
    new_password: str

class TestSmtpPayload(BaseModel):
    recipient_email: Optional[str] = ""

class SurveyRecordModel(BaseModel):
    client_uuid: str
    region: Optional[str] = "Thrissur"
    center: Optional[str] = ""
    rt_room: Optional[str] = ""
    technology: Optional[str] = "GPON"
    olt_name: Optional[str] = ""
    port_number: Optional[str] = "P1"
    kseb_post_number: str
    landmark: Optional[str] = ""
    enclosure_number: Optional[str] = "E1"
    enclosure_id: Optional[str] = ""
    lat_long: Optional[str] = ""
    splitter_id: Optional[str] = "S1"
    splitter_ratio: Optional[str] = "1:8"
    customers_connected: Optional[int] = 0
    splitter_lead_color: Optional[str] = ""
    adl_subscriber_id: Optional[str] = ""
    acs_subscriber_id: Optional[str] = ""
    survey_date_time: Optional[str] = None
    device_id: Optional[str] = "unknown_device"
    surveyor_username: Optional[str] = None
    surveyor_name: Optional[str] = None
    created_at: Optional[str] = None

class SyncPayload(BaseModel):
    device_id: Optional[str] = "android_mobile"
    records: List[SurveyRecordModel]

class OLTEditModel(BaseModel):
    region: Optional[str] = "Thrissur"
    center: str
    rt_room: str
    tech: Optional[str] = "GPON"
    olt_name: str
    olt_type: Optional[str] = "8 P"
    port_count: Optional[int] = 8
    old_center: Optional[str] = None
    old_rt_room: Optional[str] = None
    old_olt_name: Optional[str] = None

class OLTDeleteModel(BaseModel):
    center: str
    rt_room: str
    olt_name: str

class UpdateSurveyRecordModel(BaseModel):
    kseb_post_number: Optional[str] = None
    landmark: Optional[str] = None
    lat_long: Optional[str] = None
    customers_connected: Optional[int] = 0
    splitter_lead_color: Optional[str] = None
    splitter_ratio: Optional[str] = None
    adl_subscriber_id: Optional[str] = None
    acs_subscriber_id: Optional[str] = None
    port_number: Optional[str] = None
    enclosure_number: Optional[str] = None
    enclosure_id: Optional[str] = None
    splitter_id: Optional[str] = None

def compute_server_enclosure_id(olt_name: str, port: str, enclosure: str) -> str:
    if not olt_name:
        return ""
    s = olt_name.strip()
    m = re.search(r'([A-Za-z]{2,5})[\/\-_ ]*(\d+)[\/\-_ ]*(?:[A-Za-z]{2,5}[\/\-_ ]*)?(?:8\s*PORT\s+)?OLT[\/\-_ ]*0*(\d+)', s, re.I)
    if m:
        pref = m.group(1).upper()
        site = m.group(2)
        olt_num = m.group(3).zfill(2)
        olt_code = f"{pref}{site}OLT{olt_num}"
    elif re.search(r'OLT[- ]*HEADEND', s, re.I):
        m2 = re.search(r'([A-Za-z]{2,5})[\/\-_ ]*(\d+)', s, re.I)
        m_num = re.search(r'HEADEND\s*(\d+)', s, re.I)
        olt_num = m_num.group(1).zfill(2) if m_num else "01"
        pref = m2.group(1).upper() if m2 else "OLT"
        site = m2.group(2) if m2 else "01"
        olt_code = f"{pref}{site}OLT{olt_num}"
    elif re.search(r'AMALA[- ]*P1', s, re.I):
        m3 = re.search(r'([A-Za-z]{2,5})[\/\-_ ]*(\d+)', s, re.I)
        olt_code = f"{m3.group(1).upper()}{m3.group(2)}OLT01" if m3 else "THN77OLT01"
    else:
        olt_code = re.sub(r'[^A-Za-z0-9]', '', s).upper()[:12]

    port_code = re.sub(r'[\s,]+', '', (port or "P1").strip().upper())
    enc_code = (enclosure or "E1").strip().upper()
    return f"{olt_code}{port_code}{enc_code}"

# ====================
# AUTHENTICATION API
# ====================

@app.post("/api/login")
def login(req: LoginRequest, request: Request):
    client_ip = get_client_ip(request)
    rate_key = f"{client_ip}_{req.username.strip().lower()}"
    if not check_rate_limit(LOGIN_ATTEMPTS, rate_key, max_attempts=15, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 1 minute before trying again.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))", (req.username.strip(),))
    user = cur.fetchone()

    pwd_input = req.password
    pwd_stripped = req.password.strip()
    pwd_valid = False
    if user:
        pwd_valid = verify_password(pwd_stripped, user["password"]) or verify_password(pwd_input, user["password"])

    if not user or not pwd_valid:
        conn.close()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    # Auto-upgrade: if password is still plaintext, hash it now (seamless migration)
    if not is_hashed(user["password"]):
        hashed = hash_password(req.password.strip())
        cur.execute("UPDATE users SET password = ? WHERE username = ?", (hashed, user["username"]))
        conn.commit()
        print(f"[Auth] Auto-upgraded password hash for user: {user['username']}")
        save_users_to_json(conn)

    conn.close()

    raw_center = user["assigned_center"] or "ALL"
    raw_region = user["assigned_region"] or "Thrissur"
    centers_list = [c.strip() for c in raw_center.split(",") if c.strip()]
    regions_list = [r.strip() for r in raw_region.split(",") if r.strip()]

    user_role = normalize_role(user["role"])
    user_email = (user["email"] or "").strip() if "email" in user.keys() and user["email"] else ""

    # Issue session token
    token = create_session_token(user["username"], user_role)

    return {
        "status": "success",
        "token": token,
        "user": {
            "username": user["username"],
            "full_name": user["full_name"],
            "email": user_email,
            "assigned_center": raw_center,
            "assigned_centers": centers_list,
            "assigned_region": raw_region,
            "assigned_regions": regions_list,
            "role": user_role,
            "role_label": VALID_ROLES.get(user_role, "Field Technician")
        }
    }

@app.get("/api/user-profile")
def get_user_profile(username: Optional[str] = None, session: dict = Depends(require_any_auth)):
    uname = (username or session.get("username") or "").strip()
    if not uname:
        raise HTTPException(status_code=400, detail="Username is required")
    if session.get("role") != "super_admin" and session.get("username", "").lower() != uname.lower():
        raise HTTPException(status_code=403, detail="Permission Denied: You can only view your own user profile.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))", (uname,))
    user = cur.fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {uname} not found")
    raw_center = user["assigned_center"] or "ALL"
    raw_region = user["assigned_region"] or "Thrissur"
    centers_list = [c.strip() for c in raw_center.split(",") if c.strip()]
    regions_list = [r.strip() for r in raw_region.split(",") if r.strip()]
    user_role = normalize_role(user["role"])
    user_email = (user["email"] or "").strip() if "email" in user.keys() and user["email"] else ""
    return {
        "status": "success",
        "user": {
            "username": user["username"],
            "full_name": user["full_name"],
            "email": user_email,
            "assigned_center": raw_center,
            "assigned_centers": centers_list,
            "assigned_region": raw_region,
            "assigned_regions": regions_list,
            "role": user_role,
            "role_label": VALID_ROLES.get(user_role, "Field Technician")
        }
    }

@app.get("/api/users")
def get_users(session: dict = Depends(require_admin_auth)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT username, full_name, email, assigned_center, assigned_region, role, created_at FROM users")
    users = [
        {
            "username": u["username"],
            "full_name": u["full_name"],
            "email": u["email"] or "",
            "assigned_center": u["assigned_center"] or "ALL",
            "assigned_centers": [c.strip() for c in (u["assigned_center"] or "").split(",") if c.strip()],
            "assigned_region": u["assigned_region"] or "Thrissur",
            "assigned_regions": [r.strip() for r in (u["assigned_region"] or "Thrissur").split(",") if r.strip()],
            "role": normalize_role(u["role"]),
            "role_label": VALID_ROLES.get(normalize_role(u["role"]), "Field Technician"),
            "created_at": u["created_at"] or ""
        } for u in cur.fetchall()
    ]
    conn.close()
    return {"users": users}

@app.post("/api/users")
def create_user(u: UserCreateModel, session: dict = Depends(require_admin_auth)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()
    role_clean = normalize_role(u.role)
    email_clean = (u.email or "").strip().lower()

    # Normalize assigned_center (support list or comma string)
    if isinstance(u.assigned_center, list):
        center_str = ", ".join([c.strip() for c in u.assigned_center if c.strip()])
    else:
        center_str = (u.assigned_center or "").strip()
    if not center_str:
        center_str = "ALL"

    # Normalize assigned_region (support list or comma string)
    if isinstance(u.assigned_region, list):
        region_str = ", ".join([r.strip() for r in u.assigned_region if r.strip()])
    else:
        region_str = (u.assigned_region or "Thrissur").strip()
    if not region_str:
        region_str = "Thrissur"

    # Check if user exists and password is provided
    cur.execute("SELECT password FROM users WHERE username = ?", (u.username.strip(),))
    existing_row = cur.fetchone()
    if existing_row and not (u.password or "").strip():
        # Keep existing password (already hashed or plaintext)
        password_to_store = existing_row[0]
    else:
        # Hash the new password before storing
        raw_pwd = (u.password or "").strip() or "1234"
        password_to_store = hash_password(raw_pwd)

    try:
        cur.execute("""
        INSERT INTO users (username, password, full_name, email, assigned_center, assigned_region, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            password=excluded.password,
            full_name=excluded.full_name,
            email=excluded.email,
            assigned_center=excluded.assigned_center,
            assigned_region=excluded.assigned_region,
            role=excluded.role
        """, (u.username.strip(), password_to_store, u.full_name.strip(), email_clean, center_str, region_str, role_clean, now))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    save_users_to_json()
    return {"status": "success", "message": f"User {u.username} ({VALID_ROLES.get(role_clean, role_clean)}) saved successfully with charge of: {center_str}."}

def send_otp_email(to_email: str, recipient_name: str, otp_code: str) -> tuple[bool, str]:
    """Sends OTP verification email via configured SMTP server or logs to console if unconfigured."""
    conf = load_smtp_config()
    host = conf["host"]
    port = conf["port"]
    user = conf["user"]
    password = conf["password"]
    from_email = conf["from_email"]
    from_name = conf["from_name"]
    tls = conf["tls"]

    if not host or not user:
        print(f"\n=======================================================")
        print(f"[OTP DISPATCH (SIMULATION / LOG MODE)]")
        print(f"Recipient: {recipient_name} <{to_email}>")
        print(f"OTP Code:  {otp_code} (Valid for 10 minutes)")
        print(f"Notice: SMTP_HOST/SMTP_USER not set in .env. Logged for testing.")
        print(f"=======================================================\n")
        return True, "Simulation mode (logged to console)"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"GPON Network Mapping - Password Reset OTP: {otp_code}"
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email

        text_content = f"""Hello {recipient_name},

You requested to reset your password / PIN for the GPON Network Mapping & Survey Platform.

Your One-Time Password (OTP) is: {otp_code}

This verification code is valid for 10 minutes.
If you did not request a password reset, please ignore this email.

Best regards,
GPON Network Mapping Team
"""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
    .container {{ max-width: 500px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 28px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
    .logo {{ display: inline-block; background: #0284c7; color: #ffffff; border-radius: 8px; width: 36px; height: 36px; text-align: center; line-height: 36px; font-weight: bold; font-size: 18px; margin-bottom: 12px; }}
    .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 16px; }}
    .otp-box {{ background: #f0fdf4; border: 2px dashed #10b981; border-radius: 8px; padding: 18px; text-align: center; margin: 20px 0; }}
    .otp-code {{ font-family: 'Courier New', Courier, monospace; font-size: 32px; font-weight: 800; letter-spacing: 8px; color: #047857; margin: 0; }}
    .footer {{ font-size: 11px; color: #94a3b8; margin-top: 24px; border-top: 1px solid #f1f5f9; padding-top: 14px; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">田</div>
    <h2 class="title">GPON Network Mapping</h2>
    <p>Hello <strong>{recipient_name}</strong>,</p>
    <p>A request was received to reset the password / PIN for your surveyor account.</p>
    <div class="otp-box">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #059669; font-weight: 700; margin-bottom: 6px;">Your 6-Digit Verification OTP</div>
      <div class="otp-code">{otp_code}</div>
      <div style="font-size: 12px; color: #64748b; margin-top: 6px;">Valid for 10 minutes</div>
    </div>
    <p style="font-size: 13px; color: #64748b;">If you did not request this OTP, you can safely ignore this email. Your current password remains unchanged.</p>
    <div class="footer">
      GPON Fiber Network Mapping & Survey Platform &bull; Automated Security Service
    </div>
  </div>
</body>
</html>"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.send_message(msg)
        server.quit()
        print(f"[SMTP Success] Password reset OTP sent to {to_email}")
        return True, "Email sent successfully"
    except Exception as e:
        err_msg = str(e)
        print(f"[SMTP Error] Failed sending OTP email to {to_email}: {err_msg}")
        return False, err_msg

@app.post("/api/request-password-reset-otp")
def request_password_reset_otp(req: RequestResetOtpPayload, request: Request):
    client_ip = get_client_ip(request)
    rate_key = f"{client_ip}_{req.username_or_email.strip().lower()}"
    if not check_rate_limit(OTP_REQUEST_ATTEMPTS, rate_key, max_attempts=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many OTP requests. Please wait 5 minutes before requesting again.")

    identifier = req.username_or_email.strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Please enter your username or registered email address.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM users 
        WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) 
           OR LOWER(TRIM(email)) = LOWER(TRIM(?))
    """, (identifier, identifier))
    user = cur.fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="No user account found matching that username or email address.")

    user_email = (user["email"] or "").strip().lower()
    if not user_email or "@" not in user_email:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Account '{user['username']}' does not have a registered email address on file. Please contact your Super Administrator."
        )

    # Generate cryptographically secure 6-digit OTP
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    now = time.time()

    cur.execute("""
        INSERT INTO password_reset_otps (username, otp, expires_at, attempts, email, full_name)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            otp = excluded.otp,
            expires_at = excluded.expires_at,
            attempts = 0,
            email = excluded.email,
            full_name = excluded.full_name
    """, (user["username"].lower(), otp_code, now + OTP_EXPIRY_SECONDS, user_email, user["full_name"] or user["username"]))
    conn.commit()
    conn.close()

    # Dispatch email
    sent, error_msg = send_otp_email(user_email, user["full_name"] or user["username"], otp_code)
    smtp_conf = load_smtp_config()
    is_smtp_setup = bool(smtp_conf["host"] and smtp_conf["user"])

    if is_smtp_setup and not sent:
        # Roll back OTP from database
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM password_reset_otps WHERE username = ?", (user["username"].lower(),))
        conn.commit()
        conn.close()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to deliver OTP email: {error_msg}. Please check server SMTP configuration."
        )

    # Mask email for privacy (e.g. j***n@gmail.com)
    parts = user_email.split("@")
    if len(parts[0]) <= 2:
        masked_user = parts[0][0] + "*"
    else:
        masked_user = parts[0][0] + ("*" * (len(parts[0]) - 2)) + parts[0][-1]
    masked_email = f"{masked_user}@{parts[1]}"

    return {
        "status": "success",
        "username": user["username"],
        "masked_email": masked_email,
        "expires_in_minutes": 10,
        "smtp_configured": is_smtp_setup,
        "message": f"A 6-digit verification code has been sent to your registered email ({masked_email}). Please check your inbox."
    }

@app.post("/api/test-smtp")
def test_smtp_endpoint(payload: Optional[TestSmtpPayload] = None, session: dict = Depends(require_admin_auth)):
    conf = load_smtp_config()
    if not conf["host"] or not conf["user"]:
        return {
            "status": "warning",
            "message": "SMTP is not fully configured in .env. SMTP_HOST and SMTP_USER are currently empty (running in simulation/console mode).",
            "config": {
                "host": conf["host"] or "(not set)",
                "port": conf["port"],
                "user": conf["user"] or "(not set)",
                "from_email": conf["from_email"],
                "tls": conf["tls"]
            }
        }

    target = (payload.recipient_email if payload and payload.recipient_email else "").strip() or conf["from_email"] or conf["user"]
    if "@" not in target:
        raise HTTPException(status_code=400, detail="Please provide a valid recipient email address.")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "GPON Network Mapping - SMTP Connection Test"
        msg["From"] = f"{conf['from_name']} <{conf['from_email']}>"
        msg["To"] = target

        text_content = f"""Hello Administrator,

This is a test email sent from your GPON Network Mapping platform to confirm your SMTP configuration is active and working properly.

Configuration Details:
- SMTP Host: {conf['host']}
- Port: {conf['port']}
- User: {conf['user']}
- TLS Enabled: {conf['tls']}
- Sender: {conf['from_name']} <{conf['from_email']}>

If you received this email, password reset OTP dispatch is fully operational!
"""

        html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f8fafc; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 24px;">
    <h3 style="color: #0284c7; margin-top:0;">✅ SMTP Connection Test Successful!</h3>
    <p>Your GPON Network Mapping platform successfully connected to your SMTP mail server and delivered this test email.</p>
    <div style="background: #f1f5f9; border-radius: 8px; padding: 14px; margin: 16px 0; font-size: 13px;">
      <div><strong>Host:</strong> {conf['host']}:{conf['port']}</div>
      <div><strong>User:</strong> {conf['user']}</div>
      <div><strong>From:</strong> {conf['from_name']} &lt;{conf['from_email']}&gt;</div>
      <div><strong>TLS:</strong> {'Enabled (STARTTLS)' if conf['tls'] else 'Disabled'}</div>
    </div>
    <p style="font-size: 12px; color: #166534; background: #dcfce7; padding: 10px; border-radius: 6px; font-weight: 600;">
      Password reset OTPs are fully ready to be delivered to surveyors' registered emails.
    </p>
  </div>
</body>
</html>"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        if conf["port"] == 465:
            server = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=10)
        else:
            server = smtplib.SMTP(conf["host"], conf["port"], timeout=10)
            if conf["tls"]:
                server.starttls()

        if conf["user"] and conf["password"]:
            server.login(conf["user"], conf["password"])

        server.send_message(msg)
        server.quit()
        return {
            "status": "success",
            "message": f"Connected to {conf['host']}:{conf['port']} and test email delivered to {target}!",
            "config": {
                "host": conf["host"],
                "port": conf["port"],
                "user": conf["user"],
                "from_email": conf["from_email"],
                "tls": conf["tls"]
            }
        }
    except Exception as e:
        err_msg = str(e)
        return {
            "status": "error",
            "message": f"SMTP Connection / Auth Error: {err_msg}",
            "config": {
                "host": conf["host"],
                "port": conf["port"],
                "user": conf["user"],
                "from_email": conf["from_email"],
                "tls": conf["tls"]
            }
        }

@app.post("/api/verify-password-reset-otp")
def verify_password_reset_otp(req: VerifyResetOtpPayload):
    uname = req.username.strip().lower()
    otp_in = req.otp.strip()
    new_pwd = req.new_password.strip()

    if not uname or not otp_in or not new_pwd:
        raise HTTPException(status_code=400, detail="Username, 6-digit OTP, and new password are required.")

    if len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password / PIN must be at least 4 characters long.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM password_reset_otps WHERE username = ?", (uname,))
    otp_record = cur.fetchone()

    if not otp_record:
        conn.close()
        raise HTTPException(status_code=400, detail="No active password reset request found. Please request a new OTP.")

    if time.time() > otp_record["expires_at"]:
        cur.execute("DELETE FROM password_reset_otps WHERE username = ?", (uname,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="The OTP has expired (valid for 10 minutes). Please request a new OTP.")

    if otp_record["attempts"] >= 5:
        cur.execute("DELETE FROM password_reset_otps WHERE username = ?", (uname,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Too many invalid OTP attempts. For security, this OTP was invalidated. Please request a new OTP.")

    if otp_record["otp"] != otp_in:
        new_attempts = otp_record["attempts"] + 1
        cur.execute("UPDATE password_reset_otps SET attempts = ? WHERE username = ?", (new_attempts, uname))
        conn.commit()
        conn.close()
        remaining = 5 - new_attempts
        raise HTTPException(status_code=400, detail=f"Incorrect OTP code. Please check your email and try again ({remaining} attempts remaining).")

    # OTP is valid! Update password in SQLite with secure hash
    cur.execute("UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?", (hash_password(new_pwd), uname))
    cur.execute("DELETE FROM password_reset_otps WHERE username = ?", (uname,))
    conn.commit()
    conn.close()

    # Save to JSON config so git deployments preserve it
    save_users_to_json()

    return {
        "status": "success",
        "message": "Password updated successfully! You can now log in with your new password / PIN."
    }

@app.post("/api/change-password")
def change_password(req: ChangePasswordRequest, session: dict = Depends(require_any_auth)):
    uname = req.username.strip()
    email_in = req.email.strip().lower()
    new_pwd = req.new_password.strip()

    if not uname or not email_in or not new_pwd:
        raise HTTPException(status_code=400, detail="Username, registered email address, and new password are required.")

    if len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password / PIN must be at least 4 characters long.")

    # Only super_admin or the account owner can change password
    if session["username"].lower() != uname.lower() and session["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Permission Denied: You can only change your own password.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))", (uname,))
    user = cur.fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail=f"User account '{uname}' not found.")

    user_email = (user["email"] or "").strip().lower()
    if not user_email:
        conn.close()
        raise HTTPException(status_code=400, detail="This account does not have a registered email address on file. Please contact your Super Administrator to register your email.")

    if user_email != email_in:
        conn.close()
        raise HTTPException(status_code=400, detail="The entered email address does not match the registered email for this account.")

    cur.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(new_pwd), user["username"]))
    conn.commit()
    conn.close()

    save_users_to_json()
    return {"status": "success", "message": "Password updated successfully! You can now log in with your new password / PIN."}

@app.delete("/api/users/{username}")
def delete_user(username: str, session: dict = Depends(require_admin_auth)):
    if username == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete default admin user.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    save_users_to_json()
    return {"status": "success", "message": f"User {username} deleted."}

@app.get("/api/config/users")
def export_users_config(session: dict = Depends(require_admin_auth)):
    save_users_to_json()
    if os.path.exists(USERS_CONFIG_PATH):
        return FileResponse(USERS_CONFIG_PATH, media_type="application/json", filename="users_config.json")
    raise HTTPException(status_code=404, detail="Configuration not found")

@app.post("/api/config/users")
async def import_users_config(file: UploadFile = File(...), session: dict = Depends(require_admin_auth)):
    try:
        contents = await file.read()
        raw_list = json.loads(contents.decode('utf-8'))
        users_list = [u for u in raw_list if u.get("username", "").strip() not in BANNED_SAMPLE_USERS]
        with open(USERS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(users_list, f, indent=2)
        conn = sqlite3.connect(DB_PATH)
        load_users_from_json(conn)
        conn.close()
        return {"status": "success", "message": f"Successfully imported {len(users_list)} users."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import users: {str(e)}")

# ====================
# NETWORK HIERARCHY API
# ====================

@app.get("/api/hierarchy")
def get_hierarchy(response: Response, session: Optional[dict] = Depends(get_current_session)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    hier = load_hierarchy_data()
    if session and session.get("username"):
        jur = get_user_jurisdiction(session["username"])
        if jur["role"] == "rcsm":
            filtered_hier = {}
            for center, rts in hier.items():
                c_clean = center.strip()
                reg = get_region_for_center(c_clean, hier) or "Thrissur"
                if not jur["is_all_regions"] and reg.lower() not in jur["regions"]:
                    continue
                if not jur["is_all_centers"] and c_clean.lower() not in jur["centers"]:
                    continue
                filtered_hier[center] = rts
            return {"hierarchy": filtered_hier}
    return {"hierarchy": hier if hier else {}}

@app.post("/api/upload-hierarchy")
def upload_hierarchy(payload: dict, session: dict = Depends(require_admin_auth)):
    try:
        incoming = payload.get("hierarchy", payload)
        existing = load_hierarchy_data()

        # Merge incoming into existing: only ADD new centers/rt/olts, don't clobber rich metadata
        for center, rts in incoming.items():
            if not isinstance(rts, dict):
                continue
            if center not in existing:
                existing[center] = {}
            for rt, olts in rts.items():
                if not isinstance(olts, dict):
                    continue
                if rt not in existing[center]:
                    existing[center][rt] = {}
                for olt_name, olt_data in olts.items():
                    matched_key = None
                    for exist_k in list(existing[center][rt].keys()):
                        if exist_k.strip().lower() == olt_name.strip().lower():
                            matched_key = exist_k
                            break
                    target_key = matched_key if matched_key else olt_name

                    if isinstance(olt_data, list) and target_key in existing[center][rt]:
                        existing_entry = existing[center][rt][target_key]
                        if isinstance(existing_entry, dict) and "ports" in existing_entry:
                            continue
                    
                    existing[center][rt][target_key] = olt_data

        save_hierarchy_data(existing)
        return {"status": "success", "message": "Server network hierarchy merged successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-hierarchy-excel")
async def upload_hierarchy_excel(file: UploadFile = File(...), session: dict = Depends(require_admin_auth)):
    if not (file.filename.lower().endswith(".xlsx") or file.filename.lower().endswith(".xls") or file.filename.lower().endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only Excel (.xlsx/.xls) or CSV files are supported.")
    
    contents = await file.read()
    new_hierarchy = {}
    total_olts = 0
    centers_found = set()
    regions_found = set()
    
    try:
        sheet_list = []
        if file.filename.lower().endswith(".csv"):
            import csv
            reader = csv.reader(io.StringIO(contents.decode("utf-8", errors="ignore")))
            sheet_list.append(list(reader))
        else:
            wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                sheet_rows = list(ws.iter_rows(values_only=True))
                if sheet_rows and len(sheet_rows) >= 2:
                    sheet_list.append(sheet_rows)

        if not sheet_list:
            raise HTTPException(status_code=400, detail="File is empty or missing data sheets.")

        for rows in sheet_list:
            # Find header row
            header_row_idx = -1
            col_indices = {}
            for r_idx, row in enumerate(rows[:10]):
                row_str = " ".join([str(c).lower() for c in row if c])
                if "center" in row_str or "olt" in row_str or "node" in row_str:
                    header_row_idx = r_idx
                    for c_idx, val in enumerate(row):
                        if not val: continue
                        clean_k = re.sub(r'[^a-z0-9]', '', str(val).lower())
                        col_indices[clean_k] = c_idx
                    break
            
            if header_row_idx == -1:
                continue

            def get_col(row_data, *candidates, default=""):
                for cand in candidates:
                    cand_clean = re.sub(r'[^a-z0-9]', '', cand.lower())
                    if cand_clean in col_indices:
                        idx = col_indices[cand_clean]
                        if idx < len(row_data) and row_data[idx] is not None:
                            val = str(row_data[idx]).strip()
                            if val and val.lower() != cand.lower():
                                return val
                return default

            for row in rows[header_row_idx + 1:]:
                region = get_col(row, "region", "district", default="Thrissur")
                center = get_col(row, "center", "centre", "regioncenter")
                rt_room = get_col(row, "rtroom", "rt_room", "room", default="Main RT")
                tech = get_col(row, "gponftthwdm", "tech", "technology", "gpon", "ftth", "wdm", default="GPON")
                olt = get_col(row, "oltnodename", "oltname", "olt", "nodename")
                if not olt:
                    ip_val = get_col(row, "deviceip", "ip", "ipaddress")
                    if ip_val:
                        olt = f"OLT ({ip_val})" if not ip_val.lower().startswith("olt") else ip_val

                olt_type = get_col(row, "olttype", "type", "ports", default="8 P")

                # Clean & normalize strings
                region = str(region).strip() if region else "Thrissur"
                center = str(center).strip()
                rt_room = str(rt_room).strip()
                tech = str(tech).strip() if tech else "GPON"
                olt = re.sub(r'\s+', ' ', str(olt).strip())
                olt_type = str(olt_type).strip() if olt_type else "8 P"

                if center and olt and center.lower() != "center" and not olt.lower().startswith("olt/node"):
                    if center not in new_hierarchy:
                        new_hierarchy[center] = {}
                    if rt_room not in new_hierarchy[center]:
                        new_hierarchy[center][rt_room] = {}

                    port_count = 8
                    if "16" in str(olt_type): port_count = 16
                    elif "32" in str(olt_type): port_count = 32

                    existing_match = None
                    for existing_k in new_hierarchy[center][rt_room].keys():
                        if existing_k.strip().lower() == olt.lower():
                            existing_match = existing_k
                            break

                    olt_record = {
                        "region": region,
                        "tech": tech,
                        "olt_type": olt_type if olt_type else f"{port_count} P",
                        "ports": [f"P{i+1}" for i in range(port_count)]
                    }

                    if existing_match:
                        new_hierarchy[center][rt_room][existing_match] = olt_record
                    else:
                        centers_found.add(center)
                        regions_found.add(region)
                        total_olts += 1
                        new_hierarchy[center][rt_room][olt] = olt_record

        if total_olts == 0:
            raise HTTPException(status_code=400, detail="No valid Center and OLT rows found in uploaded sheet.")

        # Load existing hierarchy and safely merge new regions, centers, RT rooms, and OLTs
        existing_hierarchy = load_hierarchy_data()

        for c, rts in new_hierarchy.items():
            matched_c = next((k for k in existing_hierarchy.keys() if k.strip().lower() == c.strip().lower()), None)
            target_c = matched_c if matched_c else c.strip()
            if target_c not in existing_hierarchy:
                existing_hierarchy[target_c] = {}

            for rt, olts in rts.items():
                matched_rt = next((k for k in existing_hierarchy[target_c].keys() if k.strip().lower() == rt.strip().lower()), None)
                target_rt = matched_rt if matched_rt else rt.strip()
                if target_rt not in existing_hierarchy[target_c]:
                    existing_hierarchy[target_c][target_rt] = {}

                for olt_k, olt_data in olts.items():
                    matched_olt = next((k for k in existing_hierarchy[target_c][target_rt].keys() if k.strip().lower() == olt_k.strip().lower()), None)
                    target_olt = matched_olt if matched_olt else olt_k.strip()
                    existing_hierarchy[target_c][target_rt][target_olt] = olt_data

        clean_hierarchy = prune_hierarchy(existing_hierarchy)
        save_hierarchy_data(clean_hierarchy)

        total_nodes_now = sum(len(olts) for c_data in clean_hierarchy.values() for olts in c_data.values())
        total_centers_now = len(clean_hierarchy)

        return {
            "status": "success",
            "message": f"Successfully merged {total_olts} Nodes from {len(centers_found)} Center(s)! Node Master now holds {total_nodes_now} total Nodes across {total_centers_now} Centers.",
            "total_olts": total_olts,
            "centers": list(centers_found),
            "hierarchy": clean_hierarchy
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse Excel: {str(e)}")

@app.post("/api/hierarchy/olt")
def save_or_edit_olt(payload: OLTEditModel, session: dict = Depends(require_admin_auth)):
    hierarchy = load_hierarchy_data()
    
    # If old keys provided, remove old location (for rename/move)
    if payload.old_center and payload.old_rt_room and payload.old_olt_name:
        try:
            old_c = payload.old_center.strip()
            old_rt = payload.old_rt_room.strip()
            old_olt = payload.old_olt_name.strip()
            if old_c in hierarchy and old_rt in hierarchy[old_c]:
                for exist_k in list(hierarchy[old_c][old_rt].keys()):
                    if exist_k.strip().lower() == old_olt.lower():
                        del hierarchy[old_c][old_rt][exist_k]
                        break
                if not hierarchy[old_c][old_rt]:
                    del hierarchy[old_c][old_rt]
                if not hierarchy[old_c]:
                    del hierarchy[old_c]
        except Exception as e:
            print("Error clearing old OLT position:", e)

    c = payload.center.strip()
    rt = payload.rt_room.strip()
    olt = re.sub(r'\s+', ' ', payload.olt_name.strip())
    reg = payload.region.strip() if payload.region else "Thrissur"
    tech = payload.tech.strip() if payload.tech else "GPON"
    o_type = payload.olt_type.strip() if payload.olt_type else "8 P"

    if not c or not rt or not olt:
        raise HTTPException(status_code=400, detail="Center, RT Room, and Node Name are required.")

    if c not in hierarchy:
        hierarchy[c] = {}
    if rt not in hierarchy[c]:
        hierarchy[c][rt] = {}
    
    # Check if duplicate exists with different casing
    for existing_k in list(hierarchy[c][rt].keys()):
        if existing_k.strip().lower() == olt.lower() and existing_k != olt:
            del hierarchy[c][rt][existing_k]

    port_cnt = payload.port_count or (16 if "16" in o_type else (32 if "32" in o_type else 8))
    ports = [f"P{i+1}" for i in range(port_cnt)]
    hierarchy[c][rt][olt] = {
        "region": reg,
        "tech": tech,
        "olt_type": o_type,
        "ports": ports
    }

    save_hierarchy_data(hierarchy)

    return {"status": "success", "message": f"Node '{olt}' saved successfully!", "hierarchy": hierarchy}

@app.delete("/api/hierarchy/olt")
def delete_olt(payload: OLTDeleteModel, session: dict = Depends(require_admin_auth)):
    hierarchy = load_hierarchy_data()
    c = payload.center.strip()
    rt = payload.rt_room.strip()
    olt = payload.olt_name.strip()

    matched_c = next((k for k in hierarchy.keys() if k.strip().lower() == c.lower()), None)
    if matched_c:
        matched_rt = next((k for k in hierarchy[matched_c].keys() if k.strip().lower() == rt.lower()), None)
        if matched_rt:
            deleted = False
            for k in list(hierarchy[matched_c][matched_rt].keys()):
                if k.strip().lower() == olt.lower():
                    del hierarchy[matched_c][matched_rt][k]
                    deleted = True
                    break
            if deleted:
                save_hierarchy_data(hierarchy)
                return {"status": "success", "message": f"Deleted Node '{olt}' successfully."}

    raise HTTPException(status_code=404, detail="Node not found in hierarchy.")

@app.post("/api/hierarchy/bulk-delete")
def bulk_delete_olts(payload: dict, session: dict = Depends(require_admin_auth)):
    """Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]}"""
    hierarchy = load_hierarchy_data()
    items = payload.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items provided for deletion.")
    
    deleted_count = 0
    for item in items:
        c = str(item.get("center", "")).strip()
        rt = str(item.get("rt_room", "")).strip()
        olt = str(item.get("olt_name", "")).strip()
        
        matched_c = next((k for k in hierarchy.keys() if k.strip().lower() == c.lower()), None)
        if matched_c:
            matched_rt = next((k for k in hierarchy[matched_c].keys() if k.strip().lower() == rt.lower()), None)
            if matched_rt:
                for k in list(hierarchy[matched_c][matched_rt].keys()):
                    if k.strip().lower() == olt.lower():
                        del hierarchy[matched_c][matched_rt][k]
                        deleted_count += 1
                        break

    save_hierarchy_data(hierarchy)
    return {"status": "success", "message": f"Deleted {deleted_count} Node(s) successfully.", "deleted": deleted_count}

@app.delete("/api/hierarchy/clear")
def clear_hierarchy(session: dict = Depends(require_admin_auth)):
    save_hierarchy_data({})
    return {"status": "success", "message": "All hierarchy data cleared."}

@app.delete("/api/hierarchy/center")
def delete_hierarchy_center(payload: dict, session: dict = Depends(require_admin_auth)):
    center = (payload.get("center") or "").strip()
    if not center:
        raise HTTPException(status_code=400, detail="Center name is required.")
    hierarchy = load_hierarchy_data()
    deleted = False
    for k in list(hierarchy.keys()):
        if k.strip().lower() == center.lower():
            del hierarchy[k]
            deleted = True
            break
    if deleted:
        save_hierarchy_data(hierarchy)
        return {"status": "success", "message": f"Center '{center}' removed from hierarchy."}
    return {"status": "success", "message": f"Center '{center}' was not in hierarchy."}

@app.get("/api/download-hierarchy-template")
def download_hierarchy_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Network_Hierarchy"

    headers = ['Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', 'OLT/Node Name', 'OLT Type']
    header_fill = PatternFill(start_color="0F9D58", end_color="0F9D58", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    sample_rows = [
        ["Thrissur", "THRISSUR NORTH", "Mulamkunnathukavu", "GPON", "THN156 OLT53 Mulamkunnathukavu", "8P"],
        ["Thrissur", "THRISSUR NORTH", "Mulamkunnathukavu", "GPON", "THN-156-OLT-53- Mulamkunnathukavu", "8P"],
        ["Palakkad", "THATHAMANGALAM", "Kollengode", "GPON", "TMM/25/OLT-08-KOLLEMGODE", "8P"],
        ["Palakkad", "PALAKKAD SOUTH", "Alathur", "GPON", "PLK/12/OLT-03-ALATHUR", "16P"]
    ]

    for r_idx, row in enumerate(sample_rows, 2):
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name="Calibri", size=10)

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

    excel_bytes = workbook_to_bytes(wb)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Network_Hierarchy_Template.xlsx"'}
    )

# ====================
# SYNC & DATA API
# ====================

@app.get("/api/health")
def health_check():
    hier_mtime = int(os.path.getmtime(HIERARCHY_FILE)) if os.path.exists(HIERARCHY_FILE) else 0
    return {
        "status": "ok",
        "hierarchy_version": hier_mtime,
        "server_time": datetime.datetime.now().isoformat()
    }

def get_color_variants(color: Optional[str]) -> list:
    if not color:
        return []
    c = str(color).strip().upper()
    if not c:
        return []
    variants = [c]
    import re
    m = re.match(r"^OUT\s+(\d+)\s*-\s*(.+)$", c)
    if m:
        port_num = int(m.group(1))
        col_name = m.group(2).strip()
        # Only port 1-12 can alias to plain color name (prevents Out 13/25 colliding with Out 1)
        if port_num <= 12 and col_name not in variants:
            variants.append(col_name)
    else:
        out1 = f"OUT 1 - {c}"
        if out1 not in variants:
            variants.append(out1)
    return variants

@app.get("/api/surveyed-points")
def get_surveyed_points(center: Optional[str] = None, session: dict = Depends(require_any_auth)):
    """
    Returns an index map of all surveyed enclosure/splitter points across the network.
    Used by mobile clients for duplicate detection, locking, multi-lead customer mapping, and ACSO supervisor updates.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    query = """
        SELECT client_uuid, region, center, rt_room, technology, olt_name, port_number,
               enclosure_number, enclosure_id, splitter_id, splitter_ratio,
               customers_connected, splitter_lead_color, adl_subscriber_id, acs_subscriber_id,
               kseb_post_number, landmark, lat_long, survey_date_time,
               surveyor_username, surveyor_name, created_at
        FROM survey_records
    """
    params = []
    if center and center.strip() and center.strip().upper() != "ALL":
        query += " WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))"
        params.append(center.strip())
        
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    
    points = {}
    for r in rows:
        enc_id = (r["enclosure_id"] or "").strip()
        spl_id = (r["splitter_id"] or "").strip()
        lead_color = (r["splitter_lead_color"] or "").strip()
        if enc_id and spl_id:
            item = {
                "client_uuid": r["client_uuid"],
                "region": r["region"] or "",
                "center": r["center"] or "",
                "rt_room": r["rt_room"] or "",
                "technology": r["technology"] or "GPON",
                "olt_name": r["olt_name"] or "",
                "port_number": r["port_number"] or "",
                "enclosure_number": r["enclosure_number"] or "",
                "enclosure_id": enc_id,
                "splitter_id": spl_id,
                "splitter_ratio": r["splitter_ratio"] or "",
                "customers_connected": r["customers_connected"] or 0,
                "splitter_lead_color": lead_color,
                "adl_subscriber_id": r["adl_subscriber_id"] or "",
                "acs_subscriber_id": r["acs_subscriber_id"] or "",
                "kseb_post_number": r["kseb_post_number"] or "",
                "landmark": r["landmark"] or "",
                "lat_long": r["lat_long"] or "",
                "survey_date_time": r["survey_date_time"] or "",
                "surveyor_username": r["surveyor_username"] or "",
                "surveyor_name": r["surveyor_name"] or r["surveyor_username"] or "Surveyor"
            }
            # 1. Lead-specific keys (including variants)
            variants = get_color_variants(lead_color)
            for v in variants:
                lead_key = f"{enc_id}|{spl_id}|{v}".upper()
                points[lead_key] = item
            if not variants:
                lead_key = f"{enc_id}|{spl_id}|".upper()
                points[lead_key] = item

            # 2. Splitter-level aggregate summary key: f"{enc_id}|{spl_id}".upper()
            spl_key = f"{enc_id}|{spl_id}".upper()
            if spl_key not in points or points[spl_key].get("is_summary"):
                summary = points.get(spl_key, {
                    "is_summary": True,
                    "enclosure_id": enc_id,
                    "splitter_id": spl_id,
                    "splitter_ratio": r["splitter_ratio"] or "",
                    "customers_connected": r["customers_connected"] if r["customers_connected"] is not None else 0,
                    "leads": [],
                    "count": 0,
                    "kseb_post_number": r["kseb_post_number"] or "",
                    "landmark": r["landmark"] or "",
                    "lat_long": r["lat_long"] or "",
                    "surveyor_username": r["surveyor_username"] or "",
                    "surveyor_name": r["surveyor_name"] or r["surveyor_username"] or "Surveyor",
                    "survey_date_time": r["survey_date_time"] or ""
                })
                norm_c = lead_color.strip().upper()
                if norm_c and norm_c not in summary["leads"]:
                    summary["leads"].append(norm_c)
                summary["count"] = len(summary["leads"])
                if r["customers_connected"] is not None and r["customers_connected"] != "":
                    summary["customers_connected"] = r["customers_connected"]
                if r["splitter_ratio"] and not summary.get("splitter_ratio"):
                    summary["splitter_ratio"] = r["splitter_ratio"]
                if r["lat_long"] and not summary.get("lat_long"):
                    summary["lat_long"] = r["lat_long"]
                if r["landmark"] and not summary.get("landmark"):
                    summary["landmark"] = r["landmark"]
                if r["kseb_post_number"] and not summary.get("kseb_post_number"):
                    summary["kseb_post_number"] = r["kseb_post_number"]
                points[spl_key] = summary
            
    return {
        "status": "success",
        "total_points": len(points),
        "points": points
    }

@app.post("/api/sync")
def sync_records(payload: SyncPayload, session: dict = Depends(require_any_auth)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now_str = datetime.datetime.now().isoformat()
    synced_uuids = []
    updated_records = []
    skipped_duplicates = []

    auth_username = session.get("username", "")
    auth_role = normalize_role(session.get("role", "field_technician"))
    is_supervisor = auth_role in ("super_admin", "admin", "supervisor", "rcsm", "acso")

    for r in payload.records:
        try:
            # Enforce authenticated surveyor identity: field technicians cannot forge username
            surveyor_user = r.surveyor_username if is_supervisor and r.surveyor_username else auth_username
            surveyor_name = r.surveyor_name if is_supervisor and r.surveyor_name else (r.surveyor_name or auth_username)

            enc_id = (r.enclosure_id or "").strip()
            spl_id = (r.splitter_id or "").strip()
            lead_color = (r.splitter_lead_color or "").strip()

            # Check if this specific enclosure, splitter, AND lead color already exists in database
            existing = None
            if enc_id and spl_id:
                if lead_color:
                    c_variants = get_color_variants(lead_color)
                    placeholders = ",".join(["?"] * len(c_variants))
                    cur.execute(f"""
                        SELECT client_uuid, surveyor_username, surveyor_name, survey_date_time, splitter_lead_color
                        FROM survey_records
                        WHERE UPPER(TRIM(enclosure_id)) = UPPER(TRIM(?))
                          AND UPPER(TRIM(splitter_id)) = UPPER(TRIM(?))
                          AND UPPER(TRIM(splitter_lead_color)) IN ({placeholders})
                    """, (enc_id, spl_id, *c_variants))
                    existing = cur.fetchone()
                else:
                    cur.execute("""
                        SELECT client_uuid, surveyor_username, surveyor_name, survey_date_time, splitter_lead_color
                        FROM survey_records
                        WHERE UPPER(TRIM(enclosure_id)) = UPPER(TRIM(?))
                          AND UPPER(TRIM(splitter_id)) = UPPER(TRIM(?))
                          AND (splitter_lead_color IS NULL OR TRIM(splitter_lead_color) = '')
                    """, (enc_id, spl_id))
                    existing = cur.fetchone()

            # Case A: Brand-new record OR re-sync of existing record with matching client_uuid
            if not existing or existing[0] == r.client_uuid:
                cur.execute("""
                INSERT INTO survey_records (
                    client_uuid, region, center, rt_room, technology,
                    olt_name, port_number, kseb_post_number, landmark,
                    enclosure_number, enclosure_id, lat_long, splitter_id,
                    splitter_ratio, customers_connected, splitter_lead_color,
                    adl_subscriber_id, acs_subscriber_id, survey_date_time,
                    device_id, surveyor_username, surveyor_name, created_at, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_uuid) DO UPDATE SET
                    region=excluded.region,
                    center=excluded.center,
                    rt_room=excluded.rt_room,
                    technology=excluded.technology,
                    olt_name=excluded.olt_name,
                    port_number=excluded.port_number,
                    kseb_post_number=excluded.kseb_post_number,
                    landmark=excluded.landmark,
                    enclosure_number=excluded.enclosure_number,
                    enclosure_id=excluded.enclosure_id,
                    lat_long=excluded.lat_long,
                    splitter_id=excluded.splitter_id,
                    splitter_ratio=excluded.splitter_ratio,
                    customers_connected=excluded.customers_connected,
                    splitter_lead_color=excluded.splitter_lead_color,
                    adl_subscriber_id=excluded.adl_subscriber_id,
                    acs_subscriber_id=excluded.acs_subscriber_id,
                    survey_date_time=excluded.survey_date_time,
                    surveyor_username=excluded.surveyor_username,
                    surveyor_name=excluded.surveyor_name,
                    synced_at=excluded.synced_at
                """, (
                    r.client_uuid, r.region, r.center, r.rt_room, r.technology,
                    r.olt_name, r.port_number, r.kseb_post_number, r.landmark,
                    r.enclosure_number, r.enclosure_id, r.lat_long, r.splitter_id,
                    r.splitter_ratio, r.customers_connected, r.splitter_lead_color,
                    r.adl_subscriber_id, r.acs_subscriber_id,
                    r.survey_date_time or (r.created_at[:19].replace('T', ' ') if r.created_at else now_str[:19].replace('T', ' ')),
                    payload.device_id, surveyor_user, surveyor_name,
                    r.created_at or now_str, now_str
                ))
                synced_uuids.append(r.client_uuid)

            # Case B: Record exists with a DIFFERENT client_uuid for this specific lead
            else:
                existing_uuid, orig_user, orig_name, orig_date, orig_color = existing
                if is_supervisor:
                    # OPTION C: Supervisor update rights! Overwrite master record cleanly in-place
                    cur.execute("""
                    UPDATE survey_records SET
                        client_uuid = ?,
                        region = ?, center = ?, rt_room = ?, technology = ?,
                        olt_name = ?, port_number = ?, kseb_post_number = ?, landmark = ?,
                        enclosure_number = ?, enclosure_id = ?, lat_long = ?, splitter_id = ?,
                        splitter_ratio = ?, customers_connected = ?, splitter_lead_color = ?,
                        adl_subscriber_id = ?, acs_subscriber_id = ?, survey_date_time = ?,
                        device_id = ?, surveyor_username = ?, surveyor_name = ?, synced_at = ?
                    WHERE client_uuid = ?
                    """, (
                        r.client_uuid,
                        r.region, r.center, r.rt_room, r.technology,
                        r.olt_name, r.port_number, r.kseb_post_number, r.landmark,
                        r.enclosure_number, r.enclosure_id, r.lat_long, r.splitter_id,
                        r.splitter_ratio, r.customers_connected, r.splitter_lead_color,
                        r.adl_subscriber_id, r.acs_subscriber_id,
                        r.survey_date_time or now_str[:19].replace('T', ' '),
                        payload.device_id, surveyor_user, surveyor_name, now_str,
                        existing_uuid
                    ))
                    synced_uuids.append(r.client_uuid)
                    updated_records.append({
                        "client_uuid": r.client_uuid,
                        "master_uuid": existing_uuid,
                        "enclosure_id": enc_id,
                        "splitter_id": spl_id,
                        "splitter_lead_color": lead_color
                    })
                else:
                    # OPTION D: Field Tech duplicate prevention! Block duplicate insertion
                    skipped_duplicates.append({
                        "client_uuid": r.client_uuid,
                        "enclosure_id": enc_id,
                        "splitter_id": spl_id,
                        "splitter_lead_color": lead_color,
                        "surveyor_name": orig_name or orig_user or "another technician",
                        "survey_date_time": orig_date or "",
                        "reason": f"Lead '{lead_color}' of Splitter {spl_id} under Enclosure {enc_id} was already surveyed by {orig_name or orig_user}."
                    })
        except Exception as e:
            print(f"Error syncing record {r.client_uuid}: {e}")

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM survey_records")
    total_count = cur.fetchone()[0]
    conn.close()

    return {
        "status": "success",
        "synced_count": len(synced_uuids),
        "synced_uuids": synced_uuids,
        "updated_count": len(updated_records),
        "updated_records": updated_records,
        "skipped_count": len(skipped_duplicates),
        "skipped_duplicates": skipped_duplicates,
        "total_server_records": total_count
    }


@app.get("/api/records")
def get_all_records(
    center: Optional[str] = None, 
    region: Optional[str] = None, 
    rt_room: Optional[str] = None,
    centers: Optional[str] = None,
    regions: Optional[str] = None,
    rt_rooms: Optional[str] = None,
    session: dict = Depends(require_management_auth)
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    user_role = session.get("role", "")
    username = session.get("username", "")

    raw_centers = centers or center or ""
    raw_regions = regions or region or ""
    raw_rts = rt_rooms or rt_room or ""

    centers_list = [c.strip().lower() for c in raw_centers.split(",") if c.strip() and c.strip().upper() != "ALL"]
    regions_list = [r.strip().lower() for r in raw_regions.split(",") if r.strip() and r.strip().upper() != "ALL"]
    rts_list = [rt.strip().lower() for rt in raw_rts.split(",") if rt.strip() and rt.strip().upper() != "ALL"]

    # Enforce jurisdiction boundaries for RCSM
    jur = get_user_jurisdiction(username)
    if jur["role"] == "rcsm":
        if not jur["is_all_regions"]:
            if regions_list:
                for r in regions_list:
                    if r not in jur["regions"]:
                        conn.close()
                        raise HTTPException(status_code=403, detail=f"Permission Denied: Region '{r}' is outside your assigned jurisdiction.")
            else:
                regions_list = jur["regions"]

        if not jur["is_all_centers"]:
            if centers_list:
                for c in centers_list:
                    if c not in jur["centers"]:
                        conn.close()
                        raise HTTPException(status_code=403, detail=f"Permission Denied: Center '{c}' is outside your assigned jurisdiction.")
            else:
                centers_list = jur["centers"]

    query = "SELECT * FROM survey_records WHERE 1=1"
    params = []
    if centers_list:
        placeholders = ",".join(["?"] * len(centers_list))
        query += f" AND LOWER(TRIM(center)) IN ({placeholders})"
        params.extend(centers_list)
    if regions_list:
        placeholders = ",".join(["?"] * len(regions_list))
        query += f" AND LOWER(TRIM(region)) IN ({placeholders})"
        params.extend(regions_list)
    if rts_list:
        placeholders = ",".join(["?"] * len(rts_list))
        query += f" AND LOWER(TRIM(rt_room)) IN ({placeholders})"
        params.extend(rts_list)

    query += " ORDER BY rowid DESC"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"records": rows, "count": len(rows)}

@app.put("/api/records/{client_uuid}")
def update_survey_record(client_uuid: str, record: UpdateSurveyRecordModel, session: dict = Depends(require_admin_auth)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id FROM survey_records WHERE client_uuid = ?", (client_uuid,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Survey record not found.")

    olt_name = existing[1] or ""
    new_port = record.port_number if record.port_number is not None else existing[2]
    new_enc_no = record.enclosure_number if record.enclosure_number is not None else existing[3]
    
    new_enc_id = record.enclosure_id
    if not new_enc_id and olt_name:
        new_enc_id = compute_server_enclosure_id(olt_name, new_port, new_enc_no)

    cur.execute("""
        UPDATE survey_records SET
            kseb_post_number = COALESCE(?, kseb_post_number),
            landmark = COALESCE(?, landmark),
            lat_long = COALESCE(?, lat_long),
            customers_connected = COALESCE(?, customers_connected),
            splitter_lead_color = COALESCE(?, splitter_lead_color),
            splitter_ratio = COALESCE(?, splitter_ratio),
            adl_subscriber_id = COALESCE(?, adl_subscriber_id),
            acs_subscriber_id = COALESCE(?, acs_subscriber_id),
            port_number = COALESCE(?, port_number),
            enclosure_number = COALESCE(?, enclosure_number),
            enclosure_id = COALESCE(?, enclosure_id),
            splitter_id = COALESCE(?, splitter_id)
        WHERE client_uuid = ?
    """, (
        record.kseb_post_number,
        record.landmark,
        record.lat_long,
        record.customers_connected,
        record.splitter_lead_color,
        record.splitter_ratio,
        record.adl_subscriber_id,
        record.acs_subscriber_id,
        record.port_number,
        record.enclosure_number,
        new_enc_id,
        record.splitter_id,
        client_uuid
    ))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Record updated successfully."}

@app.delete("/api/records/{client_uuid}")
def delete_record(
    client_uuid: str,
    enclosure_id: Optional[str] = Query(None),
    splitter_id: Optional[str] = Query(None),
    splitter_lead_color: Optional[str] = Query(None),
    session: dict = Depends(require_any_auth)
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    deleted = 0
    clean_uuid = (client_uuid or "").strip()
    if clean_uuid and clean_uuid != "by-point":
        cur.execute("DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)", (clean_uuid, clean_uuid))
        deleted = cur.rowcount

    # Fallback to natural key (enclosure_id + splitter_id + lead_color) if UUID did not match
    if deleted == 0 and enclosure_id and splitter_id:
        enc_clean = enclosure_id.strip()
        spl_clean = splitter_id.strip()
        col_clean = (splitter_lead_color or "").strip()
        if col_clean:
            c_variants = get_color_variants(col_clean)
            placeholders = ",".join(["?"] * len(c_variants))
            cur.execute(f"""
                DELETE FROM survey_records
                WHERE UPPER(TRIM(enclosure_id)) = UPPER(TRIM(?))
                  AND UPPER(TRIM(splitter_id)) = UPPER(TRIM(?))
                  AND UPPER(TRIM(splitter_lead_color)) IN ({placeholders})
            """, (enc_clean, spl_clean, *c_variants))
        else:
            cur.execute("""
                DELETE FROM survey_records
                WHERE UPPER(TRIM(enclosure_id)) = UPPER(TRIM(?))
                  AND UPPER(TRIM(splitter_id)) = UPPER(TRIM(?))
                  AND (splitter_lead_color IS NULL OR TRIM(splitter_lead_color) = '')
            """, (enc_clean, spl_clean))
        deleted = cur.rowcount

    conn.commit()
    conn.close()
    return {"status": "success", "message": "Record deleted successfully.", "deleted": deleted}

@app.post("/api/records/bulk-delete")
def bulk_delete_records(payload: dict, session: dict = Depends(require_any_auth)):
    uuids = payload.get("uuids", [])
    if not uuids:
        raise HTTPException(status_code=400, detail="No record UUIDs provided for deletion.")
    clean_uuids = [str(u).strip() for u in uuids if str(u).strip()]
    if not clean_uuids:
        raise HTTPException(status_code=400, detail="No valid record UUIDs provided.")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    deleted_count = 0
    chunk_size = 500
    for i in range(0, len(clean_uuids), chunk_size):
        chunk = clean_uuids[i:i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cur.execute(f"DELETE FROM survey_records WHERE client_uuid IN ({placeholders})", tuple(chunk))
        deleted_count += cur.rowcount
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": f"Successfully deleted {deleted_count} survey record(s).",
        "deleted": deleted_count
    }

@app.post("/api/records/clear-center")
def clear_center_records(payload: dict, session: dict = Depends(require_admin_auth)):
    center = (payload.get("center") or "").strip()
    region = (payload.get("region") or "").strip()
    if not center:
        raise HTTPException(status_code=400, detail="Center name is required.")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if region:
        cur.execute("DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(region)) = LOWER(TRIM(?))", (center, region))
    else:
        cur.execute("DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))", (center,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return {
        "status": "success",
        "message": f"Successfully deleted all {deleted} survey record(s) for center '{center}'.",
        "deleted": deleted
    }

@app.get("/api/export-excel")
def export_server_excel(session: dict = Depends(require_management_auth)):
    username = session.get("username", "")
    jur = get_user_jurisdiction(username)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = "SELECT * FROM survey_records WHERE 1=1"
    params = []

    if jur["role"] == "rcsm":
        if not jur["is_all_regions"]:
            placeholders = ",".join(["?"] * len(jur["regions"]))
            query += f" AND LOWER(TRIM(region)) IN ({placeholders})"
            params.extend(jur["regions"])
        if not jur["is_all_centers"]:
            placeholders = ",".join(["?"] * len(jur["centers"]))
            query += f" AND LOWER(TRIM(center)) IN ({placeholders})"
            params.extend(jur["centers"])

    query += " ORDER BY rowid ASC"
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    sheet_title = "Master_Data" if jur["is_super_admin"] else "Region_Survey_Data"
    wb = build_excel_workbook(rows, title=sheet_title)
    excel_bytes = workbook_to_bytes(wb)
    today = datetime.date.today().isoformat()
    filename_prefix = "GPON_Master_Network_Mapping" if jur["is_super_admin"] else f"GPON_{username}_Survey_Data"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_prefix}_{today}.xlsx"'
        }
    )

@app.get("/api/export-center-excel")
def export_center_excel(
    center: Optional[str] = None, 
    region: Optional[str] = None, 
    rt_room: Optional[str] = None,
    centers: Optional[str] = None,
    regions: Optional[str] = None,
    rt_rooms: Optional[str] = None,
    session: dict = Depends(require_export_auth)
):
    """Exports and downloads filtered survey Excel file streamed dynamically in-memory."""
    raw_centers = centers or center or ""
    raw_regions = regions or region or ""
    raw_rts = rt_rooms or rt_room or ""

    if not raw_centers and not raw_regions:
        raise HTTPException(status_code=400, detail="Center or Region is required.")

    user_role = session.get("role", "")
    username = session.get("username", "")

    centers_list = [c.strip().lower() for c in raw_centers.split(",") if c.strip() and c.strip().upper() != "ALL"]
    regions_list = [r.strip().lower() for r in raw_regions.split(",") if r.strip() and r.strip().upper() != "ALL"]
    rts_list = [rt.strip().lower() for rt in raw_rts.split(",") if rt.strip() and rt.strip().upper() != "ALL"]

    # For non-admin roles (RCSM, ACSO, Field Technician), verify requested center and region are within assigned jurisdiction
    if user_role not in ("super_admin", "admin", "supervisor"):
        jur = get_user_jurisdiction(username)
        if not jur["is_all_regions"]:
            if not regions_list:
                regions_list = jur["regions"]
            else:
                if not set(regions_list).issubset(set(jur["regions"])):
                    raise HTTPException(status_code=403, detail="Permission Denied: Region is outside your assigned jurisdiction.")
        if not jur["is_all_centers"]:
            if not centers_list:
                centers_list = jur["centers"]
            else:
                if not set(centers_list).issubset(set(jur["centers"])):
                    raise HTTPException(status_code=403, detail="Permission Denied: Center is outside your assigned jurisdiction.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = "SELECT * FROM survey_records WHERE 1=1"
    params = []

    if centers_list:
        placeholders = ",".join(["?"] * len(centers_list))
        query += f" AND LOWER(TRIM(center)) IN ({placeholders})"
        params.extend(centers_list)

    if regions_list:
        placeholders = ",".join(["?"] * len(regions_list))
        query += f" AND LOWER(TRIM(region)) IN ({placeholders})"
        params.extend(regions_list)

    if rts_list:
        placeholders = ",".join(["?"] * len(rts_list))
        query += f" AND LOWER(TRIM(rt_room)) IN ({placeholders})"
        params.extend(rts_list)

    query += " ORDER BY rowid ASC"
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    title_part = "Survey_Data"
    if centers_list:
        title_part = "_".join(centers_list[:3])
        if len(centers_list) > 3:
            title_part += f"_and_{len(centers_list)-3}_more"
    elif regions_list:
        title_part = "_".join(regions_list[:3])

    safe_cent = sanitize_folder_name(title_part, "Survey_Data")
    wb = build_excel_workbook(rows, title=safe_cent[:31])
    excel_bytes = workbook_to_bytes(wb)
    today = datetime.date.today().isoformat()
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_cent}_{today}.xlsx"'
        }
    )

@app.get("/api/export-data-zip")
def export_data_zip(session: dict = Depends(require_management_auth)):
    """Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory."""
    username = session.get("username", "")
    jur = get_user_jurisdiction(username)

    hierarchy = load_hierarchy_data()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = "SELECT * FROM survey_records WHERE 1=1"
    params = []
    if jur["role"] == "rcsm":
        if not jur["is_all_regions"]:
            placeholders = ",".join(["?"] * len(jur["regions"]))
            query += f" AND LOWER(TRIM(region)) IN ({placeholders})"
            params.extend(jur["regions"])
        if not jur["is_all_centers"]:
            placeholders = ",".join(["?"] * len(jur["centers"]))
            query += f" AND LOWER(TRIM(center)) IN ({placeholders})"
            params.extend(jur["centers"])

    query += " ORDER BY rowid ASC"
    cur.execute(query, params)
    all_rows = cur.fetchall()
    conn.close()

    # Group records by (region, center)
    center_rows = {}
    for r in all_rows:
        c = (r["center"] or "").strip()
        if not c:
            continue
        reg = (r["region"] or "").strip() or get_region_for_center(c, hierarchy) or "Thrissur"
        key = (reg, c)
        if key not in center_rows:
            center_rows[key] = []
        center_rows[key].append(r)

    # Also include any centers present in hierarchy that match jurisdiction
    for c, rts in hierarchy.items():
        c_clean = c.strip()
        reg = get_region_for_center(c_clean, hierarchy) or "Thrissur"
        if jur["role"] == "rcsm":
            if not jur["is_all_regions"] and reg.lower() not in jur["regions"]:
                continue
            if not jur["is_all_centers"] and c_clean.lower() not in jur["centers"]:
                continue
        key = (reg, c_clean)
        if key not in center_rows:
            center_rows[key] = []

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for (reg, cent), rows in sorted(center_rows.items()):
            safe_reg = sanitize_folder_name(reg, "Thrissur")
            safe_cent = sanitize_folder_name(cent, "General")
            wb = build_excel_workbook(rows, title=safe_cent[:31])
            excel_bytes = workbook_to_bytes(wb)
            zip_path = f"data/{safe_reg}/{safe_cent}/{safe_cent}_Survey_Data.xlsx"
            zip_file.writestr(zip_path, excel_bytes)

    zip_buffer.seek(0)
    today = datetime.date.today().isoformat()
    zip_prefix = "GPON_Survey_Data_All_Centers" if jur["is_super_admin"] else f"GPON_Survey_Data_{username}"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_prefix}_{today}.zip"'
        }
    )

@app.post("/api/upload-survey-excel")
async def upload_survey_excel(file: UploadFile = File(...), session: dict = Depends(require_admin_auth)):
    """Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory."""
    if not (file.filename.lower().endswith(".xlsx") or file.filename.lower().endswith(".xls") or file.filename.lower().endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only Excel (.xlsx/.xls) or CSV files are supported.")

    contents = await file.read()
    sheet_list = []
    if file.filename.lower().endswith(".csv"):
        import csv
        reader = csv.reader(io.StringIO(contents.decode("utf-8", errors="ignore")))
        sheet_list.append(list(reader))
    else:
        wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
        for sname in wb.sheetnames:
            ws = wb[sname]
            rows = list(ws.iter_rows(values_only=True))
            if rows and len(rows) >= 2:
                sheet_list.append(rows)

    if not sheet_list:
        raise HTTPException(status_code=400, detail="Uploaded file is empty or missing data sheets.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    imported_count = 0
    now_str = datetime.datetime.now().isoformat()

    try:
        for rows in sheet_list:
            header_idx = -1
            col_map = {}
            for r_idx, row in enumerate(rows[:10]):
                row_str = " ".join([str(c).lower() for c in row if c])
                if any(k in row_str for k in ["enclosure", "post", "kseb", "olt", "node", "center", "landmark"]):
                    header_idx = r_idx
                    for c_idx, val in enumerate(row):
                        if not val: continue
                        clean_k = re.sub(r'[^a-z0-9]', '', str(val).lower())
                        col_map[clean_k] = c_idx
                    break

            if header_idx == -1:
                continue

            def get_val(row_data, *candidates, default=""):
                for cand in candidates:
                    cand_clean = re.sub(r'[^a-z0-9]', '', cand.lower())
                    if cand_clean in col_map:
                        idx = col_map[cand_clean]
                        if idx < len(row_data) and row_data[idx] is not None:
                            val = str(row_data[idx]).strip()
                            if val and val.lower() != cand.lower():
                                return val
                return default

            for row in rows[header_idx + 1:]:
                if not any(row):
                    continue

                region = get_val(row, "region", "district", default="Thrissur")
                center = get_val(row, "center", "centre")
                rt_room = get_val(row, "rtroom", "room", default="Main RT")
                technology = get_val(row, "technology", "gponftthwdm", "tech", default="GPON")
                olt_name = get_val(row, "oltnodename", "oltname", "nodename", "olt")
                port_number = get_val(row, "portnumber", "port")
                kseb_post = get_val(row, "ksebpostnumber", "ksebpost", "postnumber", "post")
                landmark = get_val(row, "landmark", "location")
                enclosure_no = get_val(row, "enclosurenumber", "enclosureno")
                enclosure_id = get_val(row, "enclosureid", "fdbid", "boxid")
                lat_long = get_val(row, "latlong", "coordinates", "gps", "latlng")
                splitter_id = get_val(row, "splitterid")
                splitter_ratio = get_val(row, "splitterratio", "ratio")
                customers_raw = get_val(row, "customersconnected", "cust", "customers", default="0")
                splitter_color = get_val(row, "splitteroutcolourcode", "splitterleadcolor", "colourcode", "color")
                adl_id = get_val(row, "adlsubscriberid", "adlid", "adl")
                acs_id = get_val(row, "acssubscriberid", "acsid", "acs")
                survey_dt = get_val(row, "surveydatetime", "surveydate", "datetime", "date", default=now_str[:19].replace('T', ' '))
                surveyor_name = get_val(row, "surveyorname", "surveyor", "agent")
                surveyor_user = get_val(row, "surveyorusername", default="imported")
                client_uuid = get_val(row, "clientuuid", "uuid")

                if not (enclosure_id or kseb_post or olt_name or center):
                    continue

                try:
                    cust_num = int(float(customers_raw)) if customers_raw else 0
                except Exception:
                    cust_num = 0

                if not client_uuid:
                    if enclosure_id and survey_dt:
                        client_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{enclosure_id}_{splitter_id or 'S1'}_{survey_dt}"))
                    else:
                        client_uuid = str(uuid.uuid4())

                cur.execute("""
                INSERT INTO survey_records (
                    client_uuid, region, center, rt_room, technology,
                    olt_name, port_number, kseb_post_number, landmark,
                    enclosure_number, enclosure_id, lat_long, splitter_id,
                    splitter_ratio, customers_connected, splitter_lead_color,
                    adl_subscriber_id, acs_subscriber_id, survey_date_time,
                    device_id, surveyor_username, surveyor_name, created_at, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_uuid) DO UPDATE SET
                    region=excluded.region,
                    center=excluded.center,
                    rt_room=excluded.rt_room,
                    technology=excluded.technology,
                    olt_name=excluded.olt_name,
                    port_number=excluded.port_number,
                    kseb_post_number=excluded.kseb_post_number,
                    landmark=excluded.landmark,
                    enclosure_number=excluded.enclosure_number,
                    enclosure_id=excluded.enclosure_id,
                    lat_long=excluded.lat_long,
                    splitter_id=excluded.splitter_id,
                    splitter_ratio=excluded.splitter_ratio,
                    customers_connected=excluded.customers_connected,
                    splitter_lead_color=excluded.splitter_lead_color,
                    adl_subscriber_id=excluded.adl_subscriber_id,
                    acs_subscriber_id=excluded.acs_subscriber_id,
                    survey_date_time=excluded.survey_date_time,
                    surveyor_username=excluded.surveyor_username,
                    surveyor_name=excluded.surveyor_name,
                    synced_at=excluded.synced_at
                """, (
                    client_uuid, region or "Thrissur", center, rt_room, technology or "GPON",
                    olt_name, port_number, kseb_post, landmark,
                    enclosure_no, enclosure_id, lat_long, splitter_id,
                    splitter_ratio, cust_num, splitter_color,
                    adl_id, acs_id, survey_dt,
                    "excel_import", surveyor_user, surveyor_name,
                    now_str, now_str
                ))
                imported_count += 1

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": f"Successfully imported {imported_count} survey records into SQLite database!",
        "imported_count": imported_count
    }

@app.get("/api/data-folders-summary")
def get_data_folders_summary(session: dict = Depends(require_management_auth)):
    """Returns structured summary of region and center survey records with dynamic in-memory exports."""
    hierarchy = load_hierarchy_data()
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center")
    db_counts = {(r[0] or "Thrissur", (r[1] or "").strip()): r[2] for r in cur.fetchall()}
    conn.close()
    
    regions_map = {}
    for center, rts in hierarchy.items():
        reg = get_region_for_center(center, hierarchy)
        if reg not in regions_map:
            regions_map[reg] = {}
        regions_map[reg][center.strip()] = {
            "center": center.strip(),
            "region": reg,
            "records_count": db_counts.get((reg, center.strip()), 0)
        }
        
    for (reg, cent), count in db_counts.items():
        if not cent: continue
        if reg not in regions_map:
            regions_map[reg] = {}
        if cent not in regions_map[reg]:
            regions_map[reg][cent] = {
                "center": cent,
                "region": reg,
                "records_count": count
            }
            
    result_regions = {}
    total_centers = 0
    total_records = sum(db_counts.values())
    
    for reg, centers in sorted(regions_map.items()):
        safe_reg = sanitize_folder_name(reg, "Thrissur")
        center_list = []
        for cent_name, info in sorted(centers.items()):
            safe_cent = sanitize_folder_name(cent_name, "General")
            center_rel_path = f"data/{safe_reg}/{safe_cent}"
            excel_name = f"{safe_cent}_Survey_Data.xlsx"
            
            center_list.append({
                "center": cent_name,
                "region": reg,
                "folder": center_rel_path,
                "excel_file": excel_name,
                "records_count": info["records_count"]
            })
            total_centers += 1
        result_regions[reg] = center_list
        
    jur = get_user_jurisdiction(session.get("username", ""))
    if jur["role"] == "rcsm":
        filtered_regions = {}
        filtered_centers_count = 0
        filtered_records_count = 0
        for reg, clist in result_regions.items():
            if not jur["is_all_regions"] and reg.strip().lower() not in jur["regions"]:
                continue
            matched = []
            for c in clist:
                if not jur["is_all_centers"] and c["center"].strip().lower() not in jur["centers"]:
                    continue
                matched.append(c)
                filtered_records_count += c.get("records_count", 0)
            if matched:
                filtered_regions[reg] = matched
                filtered_centers_count += len(matched)
        result_regions = filtered_regions
        total_centers = filtered_centers_count
        total_records = filtered_records_count

    return {
        "status": "success",
        "data_storage": "SQLite Database (In-Memory Excel Streaming)",
        "total_regions": len(result_regions),
        "total_centers": total_centers,
        "total_records": total_records,
        "regions": result_regions
    }

@app.post("/api/resync-data-folders")
def resync_data_folders(session: dict = Depends(require_admin_auth)):
    """Optimizes server storage and recounts SQLite records."""
    ensure_directories_and_migrate()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM survey_records")
    cnt = cur.fetchone()[0]
    conn.close()
    return {"status": "success", "message": f"Storage optimized: 0 static Excel files on disk. {cnt} survey records active in SQLite.", "records_count": cnt}

# Central Office Web Dashboard
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Network Mapping - Admin & Management Portal</title>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f1f5f9; color: #0f172a; margin: 0; padding: 16px 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #cbd5e1; padding-bottom: 14px; margin-bottom: 18px; flex-wrap: wrap; gap: 12px; }
        .btn { background: #0284c7; color: white; border: none; border-radius: 8px; padding: 8px 14px; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; transition: background 0.15s ease; }
        .btn:hover { background: #0369a1; }
        .btn-green { background: #059669; }
        .btn-green:hover { background: #047857; }
        .btn-purple { background: #6366f1; }
        .btn-purple:hover { background: #4f46e5; }
        .btn-danger { background: #dc2626; }
        .btn-danger:hover { background: #b91c1c; }
        .btn-outline { background: white; color: #475569; border: 1.5px solid #cbd5e1; }
        .btn-outline:hover { background: #f8fafc; border-color: #94a3b8; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }
        .stat-card { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .stat-num { font-size: 1.85rem; font-weight: 800; color: #0284c7; }
        .stat-label { font-size: 0.82rem; color: #64748b; font-weight: 600; margin-top: 4px; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid #cbd5e1; padding-bottom: 8px; overflow-x: auto; }
        .tab-btn { background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1; padding: 9px 18px; border-radius: 8px; cursor: pointer; font-weight: 700; font-size: 0.88rem; white-space: nowrap; transition: all 0.15s ease; }
        .tab-btn.active { background: #0284c7; color: white; border-color: #0284c7; }
        .tab-btn:hover:not(.active) { background: #cbd5e1; }
        table { width: 100%; border-collapse: collapse; background: #ffffff; border-radius: 10px; overflow: hidden; font-size: 0.84rem; border: 1px solid #cbd5e1; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; color: #0f172a; }
        th { background: #f8fafc; color: #334155; font-weight: 700; border-bottom: 2px solid #cbd5e1; }
        tr:hover { background: #f8fafc; }
        .tag { font-size: 0.72rem; padding: 3px 8px; border-radius: 12px; background: #0284c7; color: white; font-weight: 700; display: inline-block; }
        .form-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; }
        input, select { background: #ffffff; color: #0f172a; border: 1.5px solid #cbd5e1; padding: 8px 12px; border-radius: 6px; outline: none; font-size: 0.85rem; }
        input:focus, select:focus { border-color: #0284c7; }
        .role-tag-super_admin { background: #6366f1; color: white; font-weight: 700; font-size: 0.72rem; padding: 2px 8px; border-radius: 12px; }
        .role-tag-rcsm { background: #0284c7; color: white; font-weight: 700; font-size: 0.72rem; padding: 2px 8px; border-radius: 12px; }
        .role-tag-acso { background: #059669; color: white; font-weight: 700; font-size: 0.72rem; padding: 2px 8px; border-radius: 12px; }
        .role-tag-field_technician { background: #64748b; color: white; font-weight: 700; font-size: 0.72rem; padding: 2px 8px; border-radius: 12px; }
      </style>
    </head>
    <body>

      <!-- Auth Overlay (Sign-in for /admin) -->
      <div id="admin-auth-overlay" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.85); backdrop-filter:blur(4px); z-index:99999; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:28px; max-width:400px; width:100%; box-shadow:0 25px 50px -12px rgba(0,0,0,0.25);">
          <div style="text-align:center; margin-bottom:20px;">
            <div style="font-size:2.5rem; margin-bottom:6px;">📡</div>
            <h2 style="margin:0; color:#0f172a; font-size:1.35rem;">Network Mapping Portal</h2>
            <p style="margin:4px 0 0 0; color:#64748b; font-size:0.83rem;">Sign in with Super Admin or RCSM account</p>
          </div>
          <form onsubmit="handleAdminLogin(event)">
            <div style="margin-bottom:12px;">
              <label style="display:block; font-size:0.78rem; font-weight:700; color:#475569; margin-bottom:4px;">Username</label>
              <input type="text" id="admin-login-user" required style="width:100%; padding:9px 12px;" placeholder="e.g. admin or username">
            </div>
            <div style="margin-bottom:16px;">
              <label style="display:block; font-size:0.78rem; font-weight:700; color:#475569; margin-bottom:4px;">Password / PIN</label>
              <div style="position:relative; display:flex; align-items:center;">
                <input type="password" id="admin-login-pass" required style="width:100%; padding:9px 40px 9px 12px;" placeholder="Password" autocapitalize="none" spellcheck="false">
                <button type="button" id="btn-toggle-admin-pass" onclick="toggleAdminPassword()" style="position:absolute; right:8px; background:none; border:none; font-size:1.15rem; cursor:pointer; padding:4px; color:#64748b;" title="Show / Hide Password">👁️</button>
              </div>
            </div>
            <div id="admin-login-error" style="display:none; background:#fee2e2; color:#991b1b; padding:8px 12px; border-radius:6px; font-size:0.82rem; margin-bottom:14px;"></div>
            <button type="submit" class="btn" style="width:100%; justify-content:center; padding:10px; font-size:0.92rem; background:#0284c7;">🔐 Sign In to Portal</button>
          </form>
          <div style="text-align:center; margin-top:16px;">
            <a href="/" style="color:#0284c7; font-size:0.82rem; text-decoration:none; font-weight:600;">📱 Open Field Survey Mobile Web App &rarr;</a>
          </div>
        </div>
      </div>

      <!-- Access Denied Modal (for ACSO / Field Tech attempting /admin) -->
      <div id="access-denied-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.85); backdrop-filter:blur(4px); z-index:99998; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:28px; max-width:440px; width:100%; box-shadow:0 25px 50px -12px rgba(0,0,0,0.25); text-align:center;">
          <div style="font-size:2.8rem; margin-bottom:10px;">⚠️</div>
          <h3 style="margin:0 0 8px 0; color:#0f172a;">Field Surveyor Account Detected</h3>
          <p id="access-denied-msg" style="color:#475569; font-size:0.88rem; line-height:1.5; margin-bottom:20px;">
            Your account is assigned for field survey data entry. The Admin & Management Portal is restricted to Super Admins and RCSMs.
          </p>
          <div style="display:flex; justify-content:center; gap:10px;">
            <a href="/" class="btn btn-green" style="padding:10px 18px; font-size:0.9rem;">📱 Go to Field Survey App</a>
            <button onclick="adminLogout()" class="btn btn-outline" style="padding:10px 14px;">Sign Out</button>
          </div>
        </div>
      </div>

      <!-- Top Header -->
      <div class="header">
        <div>
          <div style="display:flex; align-items:center; gap:10px;">
            <h1 style="margin:0; font-size:1.45rem; color:#0f172a;">📡 Network Mapping</h1>
            <span id="header-user-badge" class="tag" style="background:#6366f1;">Super Admin</span>
          </div>
          <p style="margin:4px 0 0 0; font-size:0.82rem; color:#64748b;">
            Logged in as: <strong id="header-user-name">Administrator</strong> | <span id="header-user-jurisdiction">Center: <span id="header-user-center">ALL</span></span>
          </p>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
          <input type="file" id="top-survey-upload-input" accept=".xlsx, .xls, .csv" style="display:none;" onchange="uploadSurveyExcel(event)">
          <button id="btn-top-import" class="btn" style="background:#0f766e; color:white;" onclick="document.getElementById('top-survey-upload-input').click()">📤 Import Survey Excel</button>
          <button id="btn-top-master-excel" class="btn btn-green" onclick="downloadWithAuth('/api/export-excel')">📊 Master Excel</button>
          <button id="btn-top-zip-excel" class="btn" style="background:#2563eb; color:white;" onclick="downloadWithAuth('/api/export-data-zip')">🗂️ All Centers (ZIP)</button>
          <a href="/" class="btn btn-outline" target="_blank" title="Open Field Survey Web App">📱 Field App</a>
          <button onclick="fetchData()" class="btn btn-outline">🔄 Refresh</button>
          <button onclick="adminLogout()" class="btn btn-danger" style="padding:8px 12px;" title="Sign Out">🚪 Exit</button>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <div class="tabs">
        <button id="tab-btn-feed" class="tab-btn active" onclick="switchTab('feed')">📋 Survey Feed & Center Dashboard</button>
        <button id="tab-btn-users" class="tab-btn" onclick="switchTab('users')">👥 User Access Management (4 Tiers)</button>
        <button id="tab-btn-hierarchy" class="tab-btn" onclick="switchTab('hierarchy')">📡 Upload Node Master Data</button>
        <button id="tab-btn-folders" class="tab-btn" onclick="switchTab('folders')">📁 Dynamic Region & Center Folders</button>
      </div>

      <!-- Tab 1: Survey Feed & Center-Wise Dashboard -->
      <div id="tab-feed" style="background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        
        <!-- Live Metrics Cards -->
        <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); margin-bottom:16px;">
          <div class="stat-card">
            <div class="stat-num" id="feed-stat-records">0</div>
            <div class="stat-label">Filtered Survey Records</div>
          </div>
          <div class="stat-card">
            <div class="stat-num" id="feed-stat-customers" style="color:#059669;">0</div>
            <div class="stat-label">Total Connected Customers</div>
          </div>
          <div class="stat-card">
            <div class="stat-num" id="feed-stat-enclosures" style="color:#6366f1;">0</div>
            <div class="stat-label">Unique Enclosures</div>
          </div>
          <div class="stat-card">
            <div class="stat-num" id="feed-stat-centers" style="color:#ea580c;">0</div>
            <div class="stat-label">Centers Active</div>
          </div>
        </div>

        <!-- Filter & Action Bar -->
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px; background:#f8fafc; padding:12px; border-radius:8px; border:1px solid #e2e8f0;">
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; flex:1;">
            <!-- Multi-Region Selector -->
            <div style="position:relative; min-width:150px;">
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Filter Region(s):</label>
              <button type="button" id="btn-feed-region-picker" onclick="toggleFeedMultiDropdown('feed-region-dropdown-panel')" style="width:100%; text-align:left; background:white; border:1.5px solid #cbd5e1; padding:7px 10px; border-radius:6px; font-size:0.82rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer;">
                <span id="feed-region-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:130px;"><span style="color:#94a3b8;">-- Select Region(s) --</span></span>
                <span style="font-size:0.7rem; color:#64748b; margin-left:4px;">▼</span>
              </button>
              <div id="feed-region-dropdown-panel" style="display:none; position:absolute; top:100%; left:0; z-index:1100; min-width:220px; background:white; border:1.5px solid #0284c7; border-radius:8px; padding:8px; margin-top:4px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.15); max-height:260px; overflow-y:auto;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid #e2e8f0;">
                  <span style="font-size:0.72rem; font-weight:700; color:#64748b;">Regions</span>
                  <div>
                    <button type="button" onclick="selectAllFeedRegions(true)" style="background:none; border:none; color:#0284c7; font-size:0.72rem; font-weight:700; cursor:pointer;">All</button>
                    <button type="button" onclick="selectAllFeedRegions(false)" style="background:none; border:none; color:#64748b; font-size:0.72rem; font-weight:700; cursor:pointer; margin-left:6px;">Clear</button>
                  </div>
                </div>
                <div id="feed-region-checkbox-list"></div>
              </div>
            </div>

            <!-- Multi-Center Selector -->
            <div style="position:relative; min-width:170px;">
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Filter Center(s):</label>
              <button type="button" id="btn-feed-center-picker" onclick="toggleFeedMultiDropdown('feed-center-dropdown-panel')" style="width:100%; text-align:left; background:white; border:1.5px solid #cbd5e1; padding:7px 10px; border-radius:6px; font-size:0.82rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer;" disabled>
                <span id="feed-center-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px; color:#94a3b8;">-- Select Region First --</span>
                <span style="font-size:0.7rem; color:#64748b; margin-left:4px;">▼</span>
              </button>
              <div id="feed-center-dropdown-panel" style="display:none; position:absolute; top:100%; left:0; z-index:1100; min-width:280px; background:white; border:1.5px solid #0284c7; border-radius:8px; padding:8px; margin-top:4px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.15); max-height:280px; overflow-y:auto;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid #e2e8f0; gap:6px;">
                  <input type="text" id="feed-center-search-input" placeholder="🔍 Search centers..." oninput="filterFeedCenterChecklist(this.value)" style="flex:1; padding:3px 6px; font-size:0.75rem; border:1px solid #cbd5e1; border-radius:4px; outline:none;">
                  <button type="button" onclick="selectAllFeedCenters(true)" style="background:none; border:none; color:#0284c7; font-size:0.72rem; font-weight:700; cursor:pointer;">All</button>
                  <button type="button" onclick="selectAllFeedCenters(false)" style="background:none; border:none; color:#64748b; font-size:0.72rem; font-weight:700; cursor:pointer;">Clear</button>
                </div>
                <div id="feed-center-checkbox-list"></div>
              </div>
            </div>

            <!-- Multi-RT Room Selector -->
            <div style="position:relative; min-width:170px;">
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Filter RT Room(s):</label>
              <button type="button" id="btn-feed-rt-picker" onclick="toggleFeedMultiDropdown('feed-rt-dropdown-panel')" style="width:100%; text-align:left; background:white; border:1.5px solid #cbd5e1; padding:7px 10px; border-radius:6px; font-size:0.82rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer;" disabled>
                <span id="feed-rt-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px; color:#94a3b8;">-- Select Center First --</span>
                <span style="font-size:0.7rem; color:#64748b; margin-left:4px;">▼</span>
              </button>
              <div id="feed-rt-dropdown-panel" style="display:none; position:absolute; top:100%; left:0; z-index:1100; min-width:260px; background:white; border:1.5px solid #0284c7; border-radius:8px; padding:8px; margin-top:4px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.15); max-height:280px; overflow-y:auto;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid #e2e8f0; gap:6px;">
                  <input type="text" id="feed-rt-search-input" placeholder="🔍 Search RT rooms..." oninput="filterFeedRTChecklist(this.value)" style="flex:1; padding:3px 6px; font-size:0.75rem; border:1px solid #cbd5e1; border-radius:4px; outline:none;">
                  <button type="button" onclick="selectAllFeedRTRooms(true)" style="background:none; border:none; color:#0284c7; font-size:0.72rem; font-weight:700; cursor:pointer;">All</button>
                  <button type="button" onclick="selectAllFeedRTRooms(false)" style="background:none; border:none; color:#64748b; font-size:0.72rem; font-weight:700; cursor:pointer;">Clear</button>
                </div>
                <div id="feed-rt-checkbox-list"></div>
              </div>
            </div>

            <div style="flex:1; min-width:180px;">
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Search Survey Data:</label>
              <input type="text" id="feed-search" placeholder="🔍 Search Enclosure, Post #, Landmark, Surveyor..." oninput="filterAndRenderFeed()" style="width:100%; padding:7px 10px; font-size:0.82rem; border:1.5px solid #cbd5e1; border-radius:6px;">
            </div>
          </div>

          <div style="display:flex; gap:8px; align-items:flex-end;">
            <span id="feed-selection-count" style="font-size:0.82rem; color:#dc2626; font-weight:700; align-self:center; display:none;">0 selected</span>
            <button id="btn-feed-delete-selected" onclick="deleteSelectedSurveyRecords()" class="btn btn-danger" style="font-size:0.82rem; padding:8px 14px; display:none;">
              🗑️ Delete Selected
            </button>
            <button id="btn-download-center" onclick="downloadSelectedCenterExcel()" class="btn btn-green" style="font-size:0.82rem; padding:8px 14px;">
              📥 Download Center Excel
            </button>
            <input type="file" id="tab1-survey-upload-input" accept=".xlsx, .xls, .csv" style="display:none;" onchange="uploadSurveyExcel(event)">
            <button id="btn-tab1-import" class="btn" style="background:#0f766e; color:white; font-size:0.82rem; padding:8px 12px;" onclick="document.getElementById('tab1-survey-upload-input').click()">
              📤 Import Excel (.xlsx)
            </button>
          </div>
        </div>

        <!-- Survey Records Table -->
        <div style="overflow-x:auto; max-height:560px; overflow-y:auto; border:1px solid #cbd5e1; border-radius:8px;">
          <table>
            <thead>
              <tr style="position:sticky; top:0; z-index:2; background:#f8fafc;">
                <th id="th-feed-select-all" style="width:36px; text-align:center; display:none;"><input type="checkbox" id="feed-select-all-cb" onchange="toggleSelectAllFeedRecords(this.checked)" title="Select All Records" style="cursor:pointer; width:16px; height:16px;"></th>
                <th>#</th>
                <th>Date & Time</th>
                <th>Enclosure ID</th>
                <th>Region</th>
                <th>Center / RT Room</th>
                <th>Node & Port</th>
                <th>KSEB Post #</th>
                <th>Landmark</th>
                <th>Lat / Long</th>
                <th>Cust</th>
                <th>Out Color</th>
                <th>ADL ID</th>
                <th>ACS ID</th>
                <th>Surveyor</th>
                <th id="th-record-actions" style="text-align:center;">Action</th>
              </tr>
            </thead>
            <tbody id="table-body">
              <tr><td colspan="16" style="text-align:center; padding:20px; color:#64748b;">Loading survey records...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 2: User Access Management (Super Admin Only) -->
      <div id="tab-users" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:16px;">
          <div>
            <h3 style="margin:0 0 4px 0; color:#0284c7;">Add / Manage User Access (4 Access Tiers)</h3>
            <p style="margin:0; font-size:0.82rem; color:#64748b;">
              1. <strong>Super Admin</strong>: Full admin access | 
              2. <strong>RCSM</strong>: Center dashboard, downloads & field survey entry | 
              3. <strong>ACSO</strong>: Field entry & download | 
              4. <strong>Field Tech</strong>: Field entry only
            </p>
          </div>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <button type="button" onclick="testSmtpConnection()" class="btn btn-outline" style="border-color:#0284c7; color:#0284c7; font-size:0.8rem; padding:7px 12px; font-weight:600;">📧 Test SMTP</button>
            <button type="button" onclick="openAddUserModal()" class="btn btn-green" style="font-size:0.85rem; padding:8px 16px; font-weight:700; box-shadow:0 1px 3px rgba(0,0,0,0.1);">➕ Add User</button>
            <button type="button" onclick="downloadUsersBackup()" class="btn" style="background:#0284c7; font-size:0.8rem; padding:7px 12px;">📥 Backup JSON</button>
            <button type="button" onclick="document.getElementById('import-users-file').click()" class="btn btn-outline" style="font-size:0.8rem; padding:7px 12px;">📤 Restore JSON</button>
            <input type="file" id="import-users-file" accept=".json" style="display:none;" onchange="importUsersConfig(event)">
          </div>
        </div>

        <!-- Users Table -->
        <div style="overflow-x:auto;">
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Full Name</th>
                <th>Email Address</th>
                <th>Region</th>
                <th>Assigned Center</th>
                <th>Role (User Rights)</th>
                <th>Created At</th>
                <th style="text-align:center;">Action</th>
              </tr>
            </thead>
            <tbody id="users-table-body">
              <tr><td colspan="8" style="text-align:center; padding:20px; color:#64748b;">Loading users...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Add / Edit User Pop-Up Modal Window -->
      <div id="user-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.6); backdrop-filter:blur(3px); z-index:9999; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:24px; max-width:650px; width:100%; max-height:92vh; overflow-y:auto; box-shadow:0 20px 25px -5px rgba(0,0,0,0.25), 0 8px 10px -6px rgba(0,0,0,0.1);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; border-bottom:1px solid #e2e8f0; padding-bottom:12px;">
            <h3 id="user-modal-title" style="margin:0; font-size:1.15rem; color:#0f172a; font-weight:700;">➕ Add New User</h3>
            <button type="button" onclick="closeUserModal()" style="background:none; border:none; font-size:1.5rem; line-height:1; color:#94a3b8; cursor:pointer; padding:4px 8px; border-radius:6px;">&times;</button>
          </div>

          <form onsubmit="saveUserFromModal(event)">
            <!-- Row 1: Username & Password -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px;">
              <div>
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">Username <span style="color:#ef4444;">*</span></label>
                <input type="text" id="modal-new-user" placeholder="e.g. anoop" required style="width:100%; border:1.5px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
              </div>
              <div>
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">Password / PIN <span id="modal-pass-required" style="color:#ef4444;">*</span></label>
                <input type="text" id="modal-new-pass" placeholder="PIN / Password" required style="width:100%; border:1.5px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
              </div>
            </div>

            <!-- Row 2: Full Name & Email Address -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px;">
              <div>
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">Full Name <span style="color:#ef4444;">*</span></label>
                <input type="text" id="modal-new-name" placeholder="Full Name" required style="width:100%; border:1.5px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
              </div>
              <div>
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">
                  Email Address <span style="font-size:0.7rem; color:#0284c7; font-weight:normal;">(For user password reset / change)</span>
                </label>
                <input type="email" id="modal-new-email" placeholder="e.g. anoop@bsnl.co.in" style="width:100%; border:1.5px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
              </div>
            </div>

            <!-- Row 3: Role (Defines User Rights) -->
            <div style="margin-bottom:14px;">
              <label style="display:block; font-size:0.78rem; font-weight:700; color:#0369a1; margin-bottom:4px;">👤 Role (Defines Operational User Rights) <span style="color:#ef4444;">*</span></label>
              <select id="modal-new-role" required style="width:100%; font-weight:600; border:1.5px solid #0284c7; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                <option value="field_technician">👷 Field Technician (Data Entry Only)</option>
                <option value="acso">📝 ACSO (Data Entry & Downloads)</option>
                <option value="rcsm">📊 RCSM (Dashboard, Downloads & Field Survey)</option>
                <option value="super_admin">👑 Super Admin (Full Control)</option>
              </select>
            </div>

            <!-- Row 4: Assigned Region(s) & Assigned Center(s) -->
            <div style="display:grid; grid-template-columns:1fr 1.3fr; gap:14px; margin-bottom:14px;">
              <!-- Multi-Region Selector -->
              <div style="position:relative;">
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">
                  Assigned Region(s)
                </label>
                <button type="button" id="btn-region-picker" onclick="toggleMultiDropdown('region-dropdown-panel')" style="width:100%; text-align:left; background:white; border:1.5px solid #cbd5e1; padding:8px 12px; border-radius:6px; font-size:0.83rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer;">
                  <span id="region-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"><span style="color:#94a3b8; font-weight:normal;">-- Select Region(s) --</span></span>
                  <span style="font-size:0.75rem; color:#64748b;">▼</span>
                </button>
                <div id="region-dropdown-panel" style="display:none; position:absolute; top:100%; left:0; right:0; z-index:1000; background:white; border:1.5px solid #0284c7; border-radius:8px; padding:10px; margin-top:4px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.15); max-height:220px; overflow-y:auto;">
                  <div id="region-checkbox-list"></div>
                </div>
              </div>

              <!-- Multi-Center Selector -->
              <div style="position:relative;">
                <label style="display:block; font-size:0.78rem; font-weight:700; color:#334155; margin-bottom:4px;">
                  Assigned Center(s) <span style="font-size:0.7rem; color:#0284c7; font-weight:normal;">(Select 1 or more)</span>
                </label>
                <button type="button" id="btn-center-picker" onclick="toggleMultiDropdown('center-dropdown-panel')" style="width:100%; text-align:left; background:white; border:1.5px solid #cbd5e1; padding:8px 12px; border-radius:6px; font-size:0.83rem; display:flex; justify-content:space-between; align-items:center; cursor:pointer;">
                  <span id="center-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"><span style="color:#94a3b8; font-weight:normal;">-- Select Center(s) --</span></span>
                  <span style="font-size:0.75rem; color:#64748b;">▼</span>
                </button>
                <div id="center-dropdown-panel" style="display:none; position:absolute; top:100%; left:0; right:0; min-width:280px; z-index:1000; background:white; border:1.5px solid #0284c7; border-radius:8px; padding:10px; margin-top:4px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.15); max-height:280px; overflow-y:auto;">
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid #e2e8f0;">
                    <input type="text" id="center-search-input" placeholder="🔍 Search centers..." oninput="filterCenterChecklist(this.value)" style="flex:1; padding:4px 8px; font-size:0.78rem; border:1px solid #cbd5e1; border-radius:4px; outline:none;">
                    <button type="button" onclick="selectAllCenters(true)" style="background:none; border:none; color:#0284c7; font-size:0.75rem; font-weight:700; cursor:pointer; margin-left:8px;">All</button>
                    <button type="button" onclick="selectAllCenters(false)" style="background:none; border:none; color:#64748b; font-size:0.75rem; font-weight:700; cursor:pointer; margin-left:4px;">None</button>
                  </div>
                  <div id="center-checkbox-list"></div>
                </div>
              </div>
            </div>

            <!-- Active Selected Center Tags -->
            <div style="margin-bottom:16px; padding:8px 10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px;">
              <div style="font-size:0.72rem; font-weight:700; color:#64748b; margin-bottom:6px;">Selected Center Coverage:</div>
              <div id="selected-centers-tags-bar" style="display:flex; flex-wrap:wrap; gap:6px;">
                <span style="color:#94a3b8; font-size:0.78rem; font-style:italic;">No centers selected yet. Please select from the Assigned Center(s) dropdown above.</span>
              </div>
            </div>

            <!-- Modal Action Buttons -->
            <div style="display:flex; justify-content:flex-end; gap:10px; border-top:1px solid #e2e8f0; padding-top:14px;">
              <button type="button" onclick="closeUserModal()" class="btn btn-outline" style="padding:8px 16px;">Cancel</button>
              <button type="submit" id="btn-modal-save-user" class="btn btn-green" style="padding:8px 20px; font-weight:700;">💾 Save User</button>
            </div>
          </form>
        </div>
      </div>

      <!-- Tab 3: Network Hierarchy Management (Super Admin Only) -->
      <div id="tab-hierarchy" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
          <div>
            <h3 style="margin:0 0 6px 0; color:#0284c7;">Upload Node Master Data (Region, Center, RT Room, Node Name)</h3>
            <p style="margin:0; font-size:0.83rem; color:#64748b;">
              Upload your Excel file containing <strong>Region</strong>, <strong>Center</strong>, <strong>RT Room</strong>, <strong>Node Name</strong>, and <strong>Number of Ports</strong>.
              All field devices will automatically cache this hierarchy.
            </p>
          </div>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <a href="/api/download-hierarchy-template" class="btn" style="background:#0f766e;">📥 Download Template (.xlsx)</a>
            <input type="file" id="hierarchy-upload-input" accept=".xlsx, .xls, .csv" style="display:none;" onchange="uploadHierarchyExcel(event)">
            <button class="btn btn-green" onclick="document.getElementById('hierarchy-upload-input').click()">📂 Browse & Upload Excel</button>
            <button class="btn" style="background:#0284c7; color:white;" onclick="openAddOltModal()">➕ Add Single Node</button>
            <button class="btn btn-outline" style="color:#dc2626; border-color:#dc2626;" onclick="clearAllHierarchy()">🗑️ Clear All Nodes</button>
          </div>
        </div>

        <div id="hierarchy-upload-status" style="margin-bottom:15px; font-size:0.85rem; display:none; padding:10px 14px; border-radius:8px;"></div>

        <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom:16px;">
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="hier-total-centers" style="font-size:1.5rem;">0</div>
            <div class="stat-label">Total Centers</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="hier-total-rtrooms" style="font-size:1.5rem;">0</div>
            <div class="stat-label">Total RT Rooms</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="hier-total-olts" style="font-size:1.5rem;">0</div>
            <div class="stat-label">Total Nodes</div>
          </div>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; gap:10px; flex-wrap:wrap;">
          <input type="text" id="hierarchy-search" placeholder="🔍 Search Region, Center, RT Room, or Node..." oninput="filterHierarchyTable()" style="flex:1; min-width:240px; max-width:400px;">
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <span id="selection-count" style="font-size:0.8rem; color:#64748b; font-weight:600; display:none;">0 selected</span>
            <button id="btn-delete-selected" onclick="deleteSelectedOlts()" class="btn btn-danger" style="padding:7px 12px; display:none;">🗑️ Delete Selected</button>
            <button onclick="fetchHierarchy()" class="btn btn-outline" style="padding:7px 12px;">🔄 Refresh Table</button>
          </div>
        </div>

        <div style="overflow-x:auto; max-height:480px; overflow-y:auto; border:1px solid #cbd5e1; border-radius:8px;">
          <table>
            <thead>
              <tr style="position:sticky; top:0; z-index:2; background:#f8fafc;">
                <th style="width:36px; text-align:center;"><input type="checkbox" id="select-all-cb" onchange="toggleSelectAll(this)" title="Select All"></th>
                <th>#</th>
                <th>Region</th>
                <th>Center</th>
                <th>RT Room</th>
                <th>GPON/FTTH/WDM</th>
                <th>Node Name</th>
                <th>Number of Ports</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id="hierarchy-table-body">
              <tr><td colspan="9" style="text-align:center; padding:20px; color:#64748b;">Loading network hierarchy...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 4: Dynamic Region & Center Folders (Super Admin & RCSM) -->
      <div id="tab-folders" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
          <div>
            <h3 style="margin:0 0 6px 0; color:#0284c7;">📁 Dynamic Region & Center Exports (In-Memory Streaming)</h3>
            <p style="margin:0; font-size:0.83rem; color:#64748b;">
              All survey records are stored securely in SQLite. Excel spreadsheets are generated and streamed dynamically on demand with zero disk waste on the server.
              Download any Center's spreadsheet individually, or download all Centers in a single structured ZIP archive.
            </p>
          </div>
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <button onclick="downloadWithAuth('/api/export-data-zip')" class="btn" style="background:#2563eb; color:white; font-size:0.85rem;">🗂️ Download All Centers (ZIP)</button>
            <button id="btn-optimize-storage" onclick="resyncFoldersAction()" class="btn" style="background:#0f766e; color:white; font-size:0.85rem;">🧹 Optimize Storage & Recount</button>
          </div>
        </div>

        <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom:16px;">
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-regions" style="font-size:1.5rem; color:#2563eb;">0</div>
            <div class="stat-label">Total Regions</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-centers" style="font-size:1.5rem; color:#0284c7;">0</div>
            <div class="stat-label">Total Center Exports</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-records" style="font-size:1.5rem; color:#059669;">0</div>
            <div class="stat-label">Total Survey Records</div>
          </div>
        </div>

        <div id="folders-container">
          <p style="text-align:center; padding:20px; color:#64748b;">Loading dynamic export centers...</p>
        </div>
      </div>

      <!-- Add / Edit Node Modal -->
      <div id="olt-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.5); z-index:9999; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:10px; padding:24px; max-width:520px; width:100%; box-shadow:0 10px 25px rgba(0,0,0,0.2);">
          <h3 id="olt-modal-title" style="margin-top:0; color:#0284c7;">Add / Edit Node</h3>
          <form onsubmit="saveOltModal(event)">
            <input type="hidden" id="modal-old-center">
            <input type="hidden" id="modal-old-rtroom">
            <input type="hidden" id="modal-old-olt">
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Region</label>
                <input type="text" id="modal-region" required style="width:100%;" value="Thrissur" placeholder="e.g. Thrissur">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Center</label>
                <input type="text" id="modal-center" required style="width:100%;" placeholder="e.g. CHALAKKUDY">
              </div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">RT Room</label>
                <input type="text" id="modal-rtroom" required style="width:100%;" placeholder="e.g. Potta">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">GPON / FTTH / WDM / EDFA</label>
                <select id="modal-tech" style="width:100%;">
                  <option value="GPON">GPON</option>
                  <option value="FTTH">FTTH</option>
                  <option value="WDM">WDM</option>
                  <option value="EDFA">EDFA</option>
                </select>
              </div>
            </div>
            <div style="margin-bottom:12px;">
              <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Node Name</label>
              <input type="text" id="modal-olt" required style="width:100%;" placeholder="e.g. CKY/116/OLT 01/Potta-1">
            </div>
            <div style="margin-bottom:18px;">
              <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Number of Ports</label>
              <select id="modal-ports" style="width:100%;">
                <option value="8 P">8 Port</option>
                <option value="16 P">16 Port</option>
                <option value="32 P">32 Port</option>
              </select>
            </div>
            <div style="display:flex; justify-content:flex-end; gap:8px;">
              <button type="button" class="btn btn-outline" onclick="closeOltModal()">Cancel</button>
              <button type="submit" class="btn btn-green">💾 Save Node</button>
            </div>
          </form>
        </div>
      </div>

      <!-- Edit Survey Record Modal (Super Admin) -->
      <div id="edit-survey-modal" onclick="if(event.target===this)closeEditSurveyRecordModal()" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.65); backdrop-filter:blur(3px); z-index:10000; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:24px; max-width:640px; width:100%; box-shadow:0 20px 25px -5px rgba(0,0,0,0.25); max-height:90vh; overflow-y:auto; border:1px solid #cbd5e1;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom:1px solid #e2e8f0; padding-bottom:10px;">
            <h3 style="margin:0; color:#0284c7; display:flex; align-items:center; gap:8px;">
              <span>✏️</span> Edit Survey Record
            </h3>
            <button type="button" onclick="closeEditSurveyRecordModal()" style="background:none; border:none; font-size:1.4rem; color:#64748b; cursor:pointer; line-height:1;">&times;</button>
          </div>
          <form onsubmit="saveEditedSurveyRecord(event)">
            <input type="hidden" id="edit-rec-uuid">

            <!-- Read-only Context Box -->
            <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px 14px; margin-bottom:14px; font-size:0.83rem; color:#334155; line-height:1.6;">
              <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px;">
                <span><strong>Enclosure ID:</strong> <span id="edit-rec-enclosure-id" style="color:#0284c7; font-weight:700; font-family:monospace;"></span></span>
                <span><strong>Splitter:</strong> <span id="edit-rec-splitter-id" style="font-weight:700; color:#0f172a;"></span></span>
              </div>
              <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px; color:#64748b;">
                <span><strong>Center / RT:</strong> <span id="edit-rec-center-rt"></span></span>
                <span><strong>Node & Port:</strong> <span id="edit-rec-node-port"></span></span>
              </div>
            </div>

            <!-- Editable Fields -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">KSEB Post Number <span style="color:#ef4444;">*</span></label>
                <input type="text" id="edit-rec-post" required style="width:100%;" placeholder="e.g. OL/33/L/17/9">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Landmark</label>
                <input type="text" id="edit-rec-landmark" style="width:100%;" placeholder="e.g. Near High School">
              </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">GPS Coordinates (Lat, Long)</label>
                <input type="text" id="edit-rec-coords" style="width:100%;" placeholder="e.g. 10.779166, 76.384416">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Connected Customers</label>
                <input type="number" id="edit-rec-cust" min="0" style="width:100%;" value="0">
              </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Splitter Ratio</label>
                <select id="edit-rec-ratio" style="width:100%;">
                  <option value="1:2">1:2</option>
                  <option value="1:4">1:4</option>
                  <option value="1:8">1:8</option>
                  <option value="1:16">1:16</option>
                  <option value="1:32">1:32</option>
                </select>
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Splitter Out Colour Code</label>
                <select id="edit-rec-color" style="width:100%;">
                  <option value="">-- None / Select --</option>
                  <option value="Out 1 - Blue">Out 1 - Blue</option>
                  <option value="Out 2 - Orange">Out 2 - Orange</option>
                  <option value="Out 3 - Green">Out 3 - Green</option>
                  <option value="Out 4 - Brown">Out 4 - Brown</option>
                  <option value="Out 5 - Slate">Out 5 - Slate</option>
                  <option value="Out 6 - White">Out 6 - White</option>
                  <option value="Out 7 - Red">Out 7 - Red</option>
                  <option value="Out 8 - Black">Out 8 - Black</option>
                  <option value="Out 9 - Yellow">Out 9 - Yellow</option>
                  <option value="Out 10 - Violet">Out 10 - Violet</option>
                  <option value="Out 11 - Rose">Out 11 - Rose</option>
                  <option value="Out 12 - Aqua">Out 12 - Aqua</option>
                  <option value="Out 13 - Blue">Out 13 - Blue</option>
                  <option value="Out 14 - Orange">Out 14 - Orange</option>
                  <option value="Out 15 - Green">Out 15 - Green</option>
                  <option value="Out 16 - Brown">Out 16 - Brown</option>
                </select>
              </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">ADL Subscriber ID</label>
                <input type="text" id="edit-rec-adl" style="width:100%;" placeholder="e.g. ADL12345">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">ACS Subscriber ID</label>
                <input type="text" id="edit-rec-acs" style="width:100%;" placeholder="e.g. ACS67890">
              </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:18px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Port Number</label>
                <input type="text" id="edit-rec-port" style="width:100%;" placeholder="e.g. P1 or P1, P2">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Enclosure #</label>
                <input type="text" id="edit-rec-enc" style="width:100%;" placeholder="e.g. E15">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Splitter ID</label>
                <input type="text" id="edit-rec-splitter" list="splitter-id-options" style="width:100%;" placeholder="e.g. S1">
                <datalist id="splitter-id-options">
                  <option value="S1">
                  <option value="S2">
                  <option value="S3">
                  <option value="S4">
                  <option value="S5">
                  <option value="S6">
                  <option value="S7">
                  <option value="S8">
                </datalist>
              </div>
            </div>

            <div style="display:flex; justify-content:flex-end; gap:8px; border-top:1px solid #e2e8f0; padding-top:14px;">
              <button type="button" class="btn btn-outline" onclick="closeEditSurveyRecordModal()">Cancel</button>
              <button type="submit" class="btn btn-green">💾 Save Changes</button>
            </div>
          </form>
        </div>
      </div>

      <!-- UI Confirmation Modal -->
      <div id="confirm-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.65); backdrop-filter:blur(3px); z-index:10001; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:24px; max-width:440px; width:100%; box-shadow:0 20px 25px -5px rgba(0,0,0,0.25); border:1px solid #cbd5e1;">
          <h3 id="confirm-modal-title" style="margin-top:0; color:#0f172a; font-size:1.15rem; display:flex; align-items:center; gap:8px;">
            ⚠️ Confirm Action
          </h3>
          <p id="confirm-modal-msg" style="color:#475569; font-size:0.92rem; line-height:1.5; margin:12px 0 24px 0;">
            Are you sure you want to proceed?
          </p>
          <div style="display:flex; justify-content:flex-end; gap:10px;">
            <button type="button" id="confirm-modal-btn-cancel" class="btn btn-outline">Cancel</button>
            <button type="button" id="confirm-modal-btn-confirm" class="btn btn-danger">Confirm</button>
          </div>
        </div>
      </div>

      <script>
        let currentAdmin = null;
        let cachedRecords = [];
        let fullHierarchyRows = [];
        let cachedHierarchy = {};

        function escapeHtml(str) {
          if (str === null || str === undefined) return '';
          return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
        }

        // Normalization
        function normalizeAdminRole(r) {
          if (!r) return 'field_technician';
          const s = String(r).toLowerCase().trim();
          if (s === 'super_admin' || s === 'superadmin' || s === 'admin') return 'super_admin';
          if (s === 'rcsm') return 'rcsm';
          if (s === 'acso') return 'acso';
          return 'field_technician';
        }

        // Check authentication & apply permissions
        function checkAdminAuth() {
          let token = localStorage.getItem('gpon_auth_token');
          let userStr = localStorage.getItem('gpon_admin_user') || localStorage.getItem('gpon_logged_in_user');
          if (!userStr || !token) {
            adminLogout();
            return false;
          }

          try {
            currentAdmin = JSON.parse(userStr);
          } catch(e) {
            adminLogout();
            return false;
          }

          const role = normalizeAdminRole(currentAdmin.role);
          currentAdmin.role = role;

          if (role === 'field_technician' || role === 'acso') {
            document.getElementById('access-denied-modal').style.display = 'flex';
            document.getElementById('access-denied-msg').innerHTML = 
              `Your account <strong>${escapeHtml(currentAdmin.username)}</strong> is registered as <strong>${role === 'acso' ? 'ACSO' : 'Field Technician'}</strong>.<br>The Admin Portal is reserved for Super Admins and RCSMs.`;
            return false;
          }

          document.getElementById('admin-auth-overlay').style.display = 'none';
          document.getElementById('access-denied-modal').style.display = 'none';

          // Apply Role UI
          applyAdminRoleUI(role);
          return true;
        }

        function toggleAdminPassword() {
          const inp = document.getElementById('admin-login-pass');
          const btn = document.getElementById('btn-toggle-admin-pass');
          if (!inp) return;
          if (inp.type === 'password') {
            inp.type = 'text';
            if (btn) btn.innerText = '🙈';
          } else {
            inp.type = 'password';
            if (btn) btn.innerText = '👁️';
          }
        }

        async function handleAdminLogin(e) {
          e.preventDefault();
          const u = document.getElementById('admin-login-user').value.trim();
          const p = document.getElementById('admin-login-pass').value.trim();
          const errEl = document.getElementById('admin-login-error');
          errEl.style.display = 'none';

          try {
            const res = await fetch('/api/login', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ username: u, password: p })
            });
            const data = await res.json();
            if (res.ok && data.status === 'success') {
              const role = normalizeAdminRole(data.user.role);
              data.user.role = role;

              if (role === 'field_technician' || role === 'acso') {
                errEl.innerText = `Access Denied: Account role is ${role === 'acso' ? 'ACSO' : 'Field Technician'}. Only Super Admin & RCSM can access Admin.`;
                errEl.style.display = 'block';
                return;
              }

              localStorage.setItem('gpon_admin_user', JSON.stringify(data.user));
              localStorage.setItem('gpon_logged_in_user', JSON.stringify(data.user));
              if (data.token) localStorage.setItem('gpon_auth_token', data.token);
              currentAdmin = data.user;
              checkAdminAuth();
              initAdminData();
            } else {
              errEl.innerText = data.detail || 'Invalid username or password';
              errEl.style.display = 'block';
            }
          } catch(err) {
            errEl.innerText = 'Network error: ' + err.message;
            errEl.style.display = 'block';
          }
        }

        function adminLogout() {
          localStorage.removeItem('gpon_admin_user');
          localStorage.removeItem('gpon_logged_in_user');
          localStorage.removeItem('gpon_auth_token');
          currentAdmin = null;
          showAdminLoginModal();
        }

        function authFetch(url, options = {}) {
          const token = localStorage.getItem('gpon_auth_token') || '';
          const headers = options.headers ? { ...options.headers } : {};
          if (token) {
            headers['Authorization'] = 'Bearer ' + token;
          }
          return fetch(url, { ...options, headers }).then(res => {
            if (res.status === 401 || res.status === 403) {
              console.warn('[Admin Auth] Session unauthenticated or token expired, redirecting to login');
              adminLogout();
            }
            return res;
          });
        }

        function downloadWithAuth(url) {
          const token = localStorage.getItem('gpon_auth_token') || '';
          const separator = url.includes('?') ? '&' : '?';
          window.location.href = `${url}${separator}token=${encodeURIComponent(token)}`;
        }

        function applyAdminRoleUI(role) {
          const nameEl = document.getElementById('header-user-name');
          const centerEl = document.getElementById('header-user-center');
          const badgeEl = document.getElementById('header-user-badge');
          const jurEl = document.getElementById('header-user-jurisdiction');

          if (currentAdmin) {
            nameEl.innerText = currentAdmin.full_name || currentAdmin.username;
            const regDisplay = currentAdmin.assigned_region || 'ALL';
            const centDisplay = currentAdmin.assigned_center || 'ALL';
            if (jurEl) {
              jurEl.innerHTML = `<strong>Region:</strong> ${escapeHtml(regDisplay)} | <strong>Center:</strong> ${escapeHtml(centDisplay)}`;
            } else if (centerEl) {
              centerEl.innerText = centDisplay;
            }
          }

          const btnUsers = document.getElementById('tab-btn-users');
          const btnHierarchy = document.getElementById('tab-btn-hierarchy');
          const btnTopImport = document.getElementById('btn-top-import');
          const btnTab1Import = document.getElementById('btn-tab1-import');
          const thRecordActions = document.getElementById('th-record-actions');
          const btnOptimize = document.getElementById('btn-optimize-storage');
          const thFeedSelectAll = document.getElementById('th-feed-select-all');
          const feedSelectionCount = document.getElementById('feed-selection-count');
          const btnFeedDeleteSelected = document.getElementById('btn-feed-delete-selected');

          if (role === 'super_admin') {
            badgeEl.innerText = '👑 Super Admin';
            badgeEl.style.background = '#6366f1';
            btnUsers.style.display = 'inline-block';
            btnHierarchy.style.display = 'inline-block';
            btnTopImport.style.display = 'inline-flex';
            btnTab1Import.style.display = 'inline-flex';
            thRecordActions.style.display = '';
            if (thFeedSelectAll) thFeedSelectAll.style.display = 'table-cell';
            if (btnOptimize) btnOptimize.style.display = 'inline-flex';
          } else if (role === 'rcsm') {
            badgeEl.innerText = '📊 RCSM';
            badgeEl.style.background = '#0284c7';
            // RCSM: Hide Tab 2 (Users) and Tab 3 (Node Master upload/edit)
            btnUsers.style.display = 'none';
            btnHierarchy.style.display = 'none';
            // RCSM: View & download only, no survey data entry or import
            btnTopImport.style.display = 'none';
            btnTab1Import.style.display = 'none';
            thRecordActions.style.display = 'none';
            if (thFeedSelectAll) thFeedSelectAll.style.display = 'none';
            if (feedSelectionCount) feedSelectionCount.style.display = 'none';
            if (btnFeedDeleteSelected) btnFeedDeleteSelected.style.display = 'none';
            if (btnOptimize) btnOptimize.style.display = 'none';

            // Ensure current tab is feed or folders
            const currentTabFeedActive = document.getElementById('tab-btn-feed').classList.contains('active');
            const currentTabFoldersActive = document.getElementById('tab-btn-folders').classList.contains('active');
            if (!currentTabFeedActive && !currentTabFoldersActive) {
              switchTab('feed');
            }
          }
        }

        function switchTab(t) {
          document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
          document.getElementById('tab-feed').style.display = 'none';
          document.getElementById('tab-users').style.display = 'none';
          document.getElementById('tab-hierarchy').style.display = 'none';
          document.getElementById('tab-folders').style.display = 'none';

          if (t === 'feed') {
            document.getElementById('tab-btn-feed').classList.add('active');
            document.getElementById('tab-feed').style.display = 'block';
            fetchData();
          } else if (t === 'users') {
            if (currentAdmin && currentAdmin.role !== 'super_admin') {
              alert('Permission Denied: User Management is reserved for Super Admin.');
              switchTab('feed');
              return;
            }
            document.getElementById('tab-btn-users').classList.add('active');
            document.getElementById('tab-users').style.display = 'block';
            fetchUsers();
          } else if (t === 'hierarchy') {
            if (currentAdmin && currentAdmin.role !== 'super_admin') {
              alert('Permission Denied: Node Master Hierarchy management is reserved for Super Admin.');
              switchTab('feed');
              return;
            }
            document.getElementById('tab-btn-hierarchy').classList.add('active');
            document.getElementById('tab-hierarchy').style.display = 'block';
            fetchHierarchy();
          } else if (t === 'folders') {
            document.getElementById('tab-btn-folders').classList.add('active');
            document.getElementById('tab-folders').style.display = 'block';
            fetchFoldersSummary();
          }
        }

        // State for Tab 1 Feed Multi-Region, Multi-Center, Multi-RT Room selection
        let selectedFeedRegions = new Set();
        let selectedFeedCenters = new Set();
        let selectedFeedRTRooms = new Set();
        let selectedFeedRecordUuids = new Set();

        function toggleSelectAllFeedRecords(checked) {
          const visibleCheckboxes = document.querySelectorAll('#table-body .feed-record-cb');
          visibleCheckboxes.forEach(cb => {
            cb.checked = checked;
            if (checked) {
              selectedFeedRecordUuids.add(cb.value);
            } else {
              selectedFeedRecordUuids.delete(cb.value);
            }
          });
          updateFeedSelectionUI();
        }

        function onSingleFeedRecordCheckboxChanged(cb) {
          if (cb.checked) {
            selectedFeedRecordUuids.add(cb.value);
          } else {
            selectedFeedRecordUuids.delete(cb.value);
          }
          updateFeedSelectionUI();
        }

        function updateFeedSelectionUI() {
          const isSuperAdmin = (currentAdmin && currentAdmin.role === 'super_admin');
          const countEl = document.getElementById('feed-selection-count');
          const btnEl = document.getElementById('btn-feed-delete-selected');
          const masterCb = document.getElementById('feed-select-all-cb');
          const thMaster = document.getElementById('th-feed-select-all');

          if (thMaster) {
            thMaster.style.display = isSuperAdmin ? 'table-cell' : 'none';
          }

          const selectedCount = selectedFeedRecordUuids.size;

          if (countEl && btnEl) {
            if (isSuperAdmin && selectedCount > 0) {
              countEl.innerText = `${selectedCount} selected`;
              countEl.style.display = 'inline-block';
              btnEl.style.display = 'inline-block';
              btnEl.innerText = `🗑️ Delete Selected (${selectedCount})`;
            } else {
              countEl.style.display = 'none';
              btnEl.style.display = 'none';
            }
          }

          if (masterCb) {
            const visibleCheckboxes = document.querySelectorAll('#table-body .feed-record-cb');
            if (visibleCheckboxes.length > 0) {
              const allChecked = Array.from(visibleCheckboxes).every(cb => cb.checked);
              const someChecked = Array.from(visibleCheckboxes).some(cb => cb.checked);
              masterCb.checked = allChecked;
              masterCb.indeterminate = (!allChecked && someChecked);
            } else {
              masterCb.checked = false;
              masterCb.indeterminate = false;
            }
          }
        }

        function toggleFeedMultiDropdown(panelId) {
          const p = document.getElementById(panelId);
          if (!p) return;
          const isShown = (p.style.display === 'block');
          ['feed-region-dropdown-panel', 'feed-center-dropdown-panel', 'feed-rt-dropdown-panel'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
          });
          p.style.display = isShown ? 'none' : 'block';
        }

        // Close feed dropdown panels on outside click
        document.addEventListener('click', (e) => {
          ['feed-region', 'feed-center', 'feed-rt'].forEach(prefix => {
            const panel = document.getElementById(`${prefix}-dropdown-panel`);
            const btn = document.getElementById(`btn-${prefix}-picker`);
            if (panel && btn && !panel.contains(e.target) && !btn.contains(e.target)) {
              panel.style.display = 'none';
            }
          });
        });

        // Tab 1: Survey Records & Center Filter
        async function fetchData() {
          const regions = Array.from(selectedFeedRegions);
          const centers = Array.from(selectedFeedCenters);
          const rtRooms = Array.from(selectedFeedRTRooms);

          // If no region or no center is selected, clear records and show prompt
          if (regions.length === 0 || centers.length === 0) {
            cachedRecords = [];
            filterAndRenderFeed();
            return;
          }

          let url = '/api/records';
          const params = [];
          if (regions.length > 0) params.push(`regions=${encodeURIComponent(regions.join(','))}`);
          if (centers.length > 0) params.push(`centers=${encodeURIComponent(centers.join(','))}`);
          if (rtRooms.length > 0) params.push(`rt_rooms=${encodeURIComponent(rtRooms.join(','))}`);
          if (params.length > 0) url += '?' + params.join('&');

          try {
            const res = await authFetch(url);
            const data = await res.json();
            cachedRecords = data.records || [];
            filterAndRenderFeed();
          } catch(e) {
            console.error('Failed to fetch records:', e);
          }
        }

        function getHierarchyMeta() {
          const regionsSet = new Set();
          const centersByRegion = {};
          const centerToRegion = {};
          const rtsByCenter = {};

          // Filter for RCSM jurisdiction
          let allowedRegions = null;
          let allowedCenters = null;
          if (currentAdmin && currentAdmin.role === 'rcsm') {
            const rawRegs = currentAdmin.assigned_regions || (currentAdmin.assigned_region ? currentAdmin.assigned_region.split(',') : []);
            const regList = rawRegs.map(r => r.trim()).filter(r => r && r.toUpperCase() !== 'ALL');
            if (regList.length > 0) {
              allowedRegions = new Set(regList.map(r => r.toLowerCase()));
            }

            const rawCents = currentAdmin.assigned_centers || (currentAdmin.assigned_center ? currentAdmin.assigned_center.split(',') : []);
            const centList = rawCents.map(c => c.trim()).filter(c => c && c.toUpperCase() !== 'ALL');
            if (centList.length > 0) {
              allowedCenters = new Set(centList.map(c => c.toLowerCase()));
            }
          }

          Object.keys(cachedHierarchy).forEach(center => {
            const rts = cachedHierarchy[center] || {};
            let centerRegion = null;
            let activeRtsInCenter = 0;
            Object.keys(rts).forEach(rt => {
              const olts = rts[rt] || {};
              const oltKeys = Object.keys(olts);
              if (oltKeys.length === 0) return;
              oltKeys.forEach(oltName => {
                const entry = olts[oltName];
                if (entry && typeof entry === 'object' && entry.region) {
                  centerRegion = entry.region.trim();
                }
              });
            });
            if (!centerRegion) centerRegion = 'Thrissur';

            // Restrict to assigned regions / centers for RCSM
            if (allowedRegions && !allowedRegions.has(centerRegion.toLowerCase())) {
              return;
            }
            if (allowedCenters && !allowedCenters.has(center.trim().toLowerCase())) {
              return;
            }

            Object.keys(rts).forEach(rt => {
              const olts = rts[rt] || {};
              const oltKeys = Object.keys(olts);
              if (oltKeys.length === 0) return;
              if (!rtsByCenter[center]) rtsByCenter[center] = new Set();
              rtsByCenter[center].add(rt);
              activeRtsInCenter++;
            });

            if (activeRtsInCenter === 0) return;

            regionsSet.add(centerRegion);
            if (!centersByRegion[centerRegion]) {
              centersByRegion[centerRegion] = new Set();
            }
            centersByRegion[centerRegion].add(center);
            centerToRegion[center] = centerRegion;
          });

          return {
            regions: Array.from(regionsSet).sort(),
            centersByRegion: centersByRegion,
            centerToRegion: centerToRegion,
            rtsByCenter: rtsByCenter,
            allCenters: Object.keys(rtsByCenter).sort()
          };
        }

        function updateFeedFilterDropdowns() {
          const meta = getHierarchyMeta();
          renderFeedRegionCheckboxes(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
        }

        function renderFeedRegionCheckboxes(meta) {
          const container = document.getElementById('feed-region-checkbox-list');
          if (!container) return;

          let html = '';
          meta.regions.forEach(reg => {
            const checked = selectedFeedRegions.has(reg);
            html += `
              <label style="display:flex; align-items:center; gap:8px; padding:4px 2px; font-size:0.82rem; cursor:pointer; color:#334155;">
                <input type="checkbox" class="feed-reg-cb" value="${escapeHtml(reg)}" ${checked ? 'checked' : ''} onchange="onFeedRegionCheckboxChanged(this)">
                <span>${escapeHtml(reg)}</span>
              </label>
            `;
          });
          container.innerHTML = html || '<div style="color:#94a3b8; font-size:0.78rem; padding:6px;">No regions loaded.</div>';
        }

        function selectAllFeedRegions(selectAll) {
          const meta = getHierarchyMeta();
          if (selectAll) {
            meta.regions.forEach(r => selectedFeedRegions.add(r));
          } else {
            selectedFeedRegions.clear();
            selectedFeedCenters.clear();
            selectedFeedRTRooms.clear();
          }
          pruneFeedSelections(meta);
          renderFeedRegionCheckboxes(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function onFeedRegionCheckboxChanged(cb) {
          const meta = getHierarchyMeta();
          if (cb.checked) {
            selectedFeedRegions.add(cb.value);
          } else {
            selectedFeedRegions.delete(cb.value);
          }
          pruneFeedSelections(meta);
          renderFeedRegionCheckboxes(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function pruneFeedSelections(meta) {
          if (selectedFeedRegions.size === 0) {
            selectedFeedCenters.clear();
            selectedFeedRTRooms.clear();
            return;
          }

          const allowedCenters = new Set();
          selectedFeedRegions.forEach(reg => {
            if (meta.centersByRegion[reg]) {
              meta.centersByRegion[reg].forEach(c => allowedCenters.add(c));
            }
          });

          selectedFeedCenters.forEach(c => {
            if (!allowedCenters.has(c)) {
              selectedFeedCenters.delete(c);
            }
          });

          const allowedRTs = new Set();
          selectedFeedCenters.forEach(c => {
            if (meta.rtsByCenter[c]) {
              meta.rtsByCenter[c].forEach(rt => allowedRTs.add(rt));
            }
          });

          selectedFeedRTRooms.forEach(rt => {
            if (!allowedRTs.has(rt)) {
              selectedFeedRTRooms.delete(rt);
            }
          });
        }

        function renderFeedCenterCheckboxes(meta) {
          const container = document.getElementById('feed-center-checkbox-list');
          if (!container) return;

          if (selectedFeedRegions.size === 0) {
            container.innerHTML = '<div style="color:#94a3b8; font-size:0.78rem; padding:8px; text-align:center;">Select at least one Region first.</div>';
            return;
          }

          const q = (document.getElementById('feed-center-search-input')?.value || '').toLowerCase().trim();

          let html = '';
          Array.from(selectedFeedRegions).sort().forEach(reg => {
            const centers = meta.centersByRegion[reg] ? Array.from(meta.centersByRegion[reg]).sort() : [];
            const filteredCenters = q ? centers.filter(c => c.toLowerCase().includes(q)) : centers;
            if (filteredCenters.length === 0) return;

            html += `
              <div style="margin-top:6px; margin-bottom:4px;">
                <div style="display:flex; justify-content:space-between; align-items:center; background:#f1f5f9; padding:3px 6px; border-radius:4px; font-size:0.73rem; font-weight:700; color:#334155; margin-bottom:3px;">
                  <span>📍 ${escapeHtml(reg)}</span>
                  <button type="button" onclick="toggleFeedRegionCenterGroup('${escapeHtml(reg)}')" style="background:none; border:none; color:#0284c7; font-size:0.7rem; cursor:pointer; font-weight:600;">Toggle</button>
                </div>
            `;
            filteredCenters.forEach(c => {
              const checked = selectedFeedCenters.has(c);
              html += `
                <label style="display:flex; align-items:center; gap:8px; padding:3px 6px; font-size:0.8rem; cursor:pointer; color:#334155;">
                  <input type="checkbox" class="feed-center-cb" data-region="${escapeHtml(reg)}" value="${escapeHtml(c)}" ${checked ? 'checked' : ''} onchange="onFeedCenterCheckboxChanged(this)">
                  <span>${escapeHtml(c)}</span>
                </label>
              `;
            });
            html += `</div>`;
          });

          container.innerHTML = html || '<div style="color:#94a3b8; font-size:0.78rem; padding:8px; text-align:center;">No matching centers found.</div>';
        }

        function filterFeedCenterChecklist(query) {
          const meta = getHierarchyMeta();
          renderFeedCenterCheckboxes(meta);
        }

        function toggleFeedRegionCenterGroup(reg) {
          const meta = getHierarchyMeta();
          const centers = meta.centersByRegion[reg] ? Array.from(meta.centersByRegion[reg]) : [];
          const allSelected = centers.every(c => selectedFeedCenters.has(c));
          if (allSelected) {
            centers.forEach(c => selectedFeedCenters.delete(c));
          } else {
            centers.forEach(c => selectedFeedCenters.add(c));
          }
          pruneFeedSelections(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function selectAllFeedCenters(selectAll) {
          const meta = getHierarchyMeta();
          if (selectAll) {
            selectedFeedRegions.forEach(reg => {
              if (meta.centersByRegion[reg]) {
                meta.centersByRegion[reg].forEach(c => selectedFeedCenters.add(c));
              }
            });
          } else {
            selectedFeedCenters.clear();
            selectedFeedRTRooms.clear();
          }
          pruneFeedSelections(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function onFeedCenterCheckboxChanged(cb) {
          const meta = getHierarchyMeta();
          if (cb.checked) {
            selectedFeedCenters.add(cb.value);
          } else {
            selectedFeedCenters.delete(cb.value);
          }
          pruneFeedSelections(meta);
          renderFeedCenterCheckboxes(meta);
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function renderFeedRTCheckboxes(meta) {
          const container = document.getElementById('feed-rt-checkbox-list');
          if (!container) return;

          if (selectedFeedCenters.size === 0) {
            container.innerHTML = '<div style="color:#94a3b8; font-size:0.78rem; padding:8px; text-align:center;">Select at least one Center first.</div>';
            return;
          }

          const q = (document.getElementById('feed-rt-search-input')?.value || '').toLowerCase().trim();

          let html = '';
          Array.from(selectedFeedCenters).sort().forEach(c => {
            const rts = meta.rtsByCenter[c] ? Array.from(meta.rtsByCenter[c]).sort() : [];
            const filteredRTs = q ? rts.filter(rt => rt.toLowerCase().includes(q)) : rts;
            if (filteredRTs.length === 0) return;

            html += `
              <div style="margin-top:6px; margin-bottom:4px;">
                <div style="display:flex; justify-content:space-between; align-items:center; background:#f1f5f9; padding:3px 6px; border-radius:4px; font-size:0.73rem; font-weight:700; color:#334155; margin-bottom:3px;">
                  <span>🏢 ${escapeHtml(c)}</span>
                  <button type="button" onclick="toggleFeedCenterRTGroup('${escapeHtml(c)}')" style="background:none; border:none; color:#0284c7; font-size:0.7rem; cursor:pointer; font-weight:600;">Toggle</button>
                </div>
            `;
            filteredRTs.forEach(rt => {
              const checked = selectedFeedRTRooms.has(rt);
              html += `
                <label style="display:flex; align-items:center; gap:8px; padding:3px 6px; font-size:0.8rem; cursor:pointer; color:#334155;">
                  <input type="checkbox" class="feed-rt-cb" data-center="${escapeHtml(c)}" value="${escapeHtml(rt)}" ${checked ? 'checked' : ''} onchange="onFeedRTCheckboxChanged(this)">
                  <span>${escapeHtml(rt)}</span>
                </label>
              `;
            });
            html += `</div>`;
          });

          container.innerHTML = html || '<div style="color:#94a3b8; font-size:0.78rem; padding:8px; text-align:center;">No matching RT rooms found.</div>';
        }

        function filterFeedRTChecklist(query) {
          const meta = getHierarchyMeta();
          renderFeedRTCheckboxes(meta);
        }

        function toggleFeedCenterRTGroup(centerName) {
          const meta = getHierarchyMeta();
          const rts = meta.rtsByCenter[centerName] ? Array.from(meta.rtsByCenter[centerName]) : [];
          const allSelected = rts.every(rt => selectedFeedRTRooms.has(rt));
          if (allSelected) {
            rts.forEach(rt => selectedFeedRTRooms.delete(rt));
          } else {
            rts.forEach(rt => selectedFeedRTRooms.add(rt));
          }
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function selectAllFeedRTRooms(selectAll) {
          const meta = getHierarchyMeta();
          if (selectAll) {
            selectedFeedCenters.forEach(c => {
              if (meta.rtsByCenter[c]) {
                meta.rtsByCenter[c].forEach(rt => selectedFeedRTRooms.add(rt));
              }
            });
          } else {
            selectedFeedRTRooms.clear();
          }
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function onFeedRTCheckboxChanged(cb) {
          if (cb.checked) {
            selectedFeedRTRooms.add(cb.value);
          } else {
            selectedFeedRTRooms.delete(cb.value);
          }
          const meta = getHierarchyMeta();
          renderFeedRTCheckboxes(meta);
          updateFeedPickerLabels();
          fetchData();
        }

        function updateFeedPickerLabels() {
          const regBtn = document.getElementById('btn-feed-region-picker');
          const regLabel = document.getElementById('feed-region-picker-label');
          const centerBtn = document.getElementById('btn-feed-center-picker');
          const centerLabel = document.getElementById('feed-center-picker-label');
          const rtBtn = document.getElementById('btn-feed-rt-picker');
          const rtLabel = document.getElementById('feed-rt-picker-label');

          if (regLabel) {
            if (selectedFeedRegions.size === 0) {
              regLabel.innerHTML = '<span style="color:#94a3b8;">-- Select Region(s) --</span>';
            } else if (selectedFeedRegions.size === 1) {
              regLabel.innerHTML = `<strong style="color:#0284c7;">${escapeHtml(Array.from(selectedFeedRegions)[0])}</strong>`;
            } else {
              regLabel.innerHTML = `<strong style="color:#0284c7;">${selectedFeedRegions.size} Regions Selected</strong>`;
            }
          }

          if (centerBtn && centerLabel) {
            if (selectedFeedRegions.size === 0) {
              centerBtn.disabled = true;
              centerLabel.innerHTML = '<span style="color:#94a3b8;">-- Select Region First --</span>';
            } else {
              centerBtn.disabled = false;
              if (selectedFeedCenters.size === 0) {
                centerLabel.innerHTML = '<span style="color:#94a3b8;">-- Select Center(s) --</span>';
              } else if (selectedFeedCenters.size === 1) {
                centerLabel.innerHTML = `<strong style="color:#0284c7;">${escapeHtml(Array.from(selectedFeedCenters)[0])}</strong>`;
              } else {
                centerLabel.innerHTML = `<strong style="color:#0284c7;">${selectedFeedCenters.size} Centers Selected</strong>`;
              }
            }
          }

          if (rtBtn && rtLabel) {
            if (selectedFeedCenters.size === 0) {
              rtBtn.disabled = true;
              rtLabel.innerHTML = '<span style="color:#94a3b8;">-- Select Center First --</span>';
            } else {
              rtBtn.disabled = false;
              if (selectedFeedRTRooms.size === 0) {
                rtLabel.innerHTML = '<span style="color:#475569; font-weight:600;">All RT Rooms</span>';
              } else if (selectedFeedRTRooms.size === 1) {
                rtLabel.innerHTML = `<strong style="color:#0284c7;">${escapeHtml(Array.from(selectedFeedRTRooms)[0])}</strong>`;
              } else {
                rtLabel.innerHTML = `<strong style="color:#0284c7;">${selectedFeedRTRooms.size} RT Rooms Selected</strong>`;
              }
            }
          }

          const dlBtn = document.getElementById('btn-download-center');
          if (dlBtn) {
            if (selectedFeedCenters.size === 1) {
              dlBtn.innerText = `📥 Download ${Array.from(selectedFeedCenters)[0]} Excel`;
            } else if (selectedFeedCenters.size > 1) {
              dlBtn.innerText = `📥 Download Filtered Excel (${selectedFeedCenters.size} Centers)`;
            } else {
              dlBtn.innerText = '📥 Download Center Excel';
            }
          }
        }

        // State for User Multi-Region and Multi-Center selection
        let selectedUserRegions = new Set();
        let selectedUserCenters = new Set();

        function toggleMultiDropdown(panelId) {
          const p = document.getElementById(panelId);
          if (!p) return;
          const isShown = (p.style.display === 'block');
          // Hide both first
          document.getElementById('region-dropdown-panel').style.display = 'none';
          document.getElementById('center-dropdown-panel').style.display = 'none';
          p.style.display = isShown ? 'none' : 'block';
        }

        // Close dropdown panels on outside click
        document.addEventListener('click', (e) => {
          const rPanel = document.getElementById('region-dropdown-panel');
          const rBtn = document.getElementById('btn-region-picker');
          const cPanel = document.getElementById('center-dropdown-panel');
          const cBtn = document.getElementById('btn-center-picker');

          if (rPanel && rBtn && !rPanel.contains(e.target) && !rBtn.contains(e.target)) {
            rPanel.style.display = 'none';
          }
          if (cPanel && cBtn && !cPanel.contains(e.target) && !cBtn.contains(e.target)) {
            cPanel.style.display = 'none';
          }
        });

        function populateUserRegionAndCenterDropdowns(preferredRegion = null, preferredCenter = null) {
          const meta = getHierarchyMeta();
          
          if (preferredRegion !== null) {
            selectedUserRegions = new Set(
              String(preferredRegion).split(',').map(s => s.trim()).filter(Boolean)
            );
          }
          if (preferredCenter !== null) {
            selectedUserCenters = new Set(
              String(preferredCenter).split(',').map(s => s.trim()).filter(Boolean)
            );
          }

          renderRegionCheckboxes(meta);
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function renderRegionCheckboxes(meta) {
          const container = document.getElementById('region-checkbox-list');
          if (!container) return;
          const isAllChecked = selectedUserRegions.has('ALL');

          let html = `
            <label style="display:flex; align-items:center; gap:8px; padding:4px 0; font-size:0.82rem; cursor:pointer; font-weight:700; border-bottom:1px solid #f1f5f9; margin-bottom:4px;">
              <input type="checkbox" id="cb-all-regions" value="ALL" ${isAllChecked ? 'checked' : ''} onchange="toggleAllRegionsCheckbox(this.checked)">
              🌐 ALL Regions
            </label>
          `;

          meta.regions.forEach(reg => {
            const checked = isAllChecked || selectedUserRegions.has(reg);
            html += `
              <label style="display:flex; align-items:center; gap:8px; padding:3px 0; font-size:0.82rem; cursor:pointer;">
                <input type="checkbox" class="user-reg-cb" value="${reg}" ${checked ? 'checked' : ''} onchange="onSingleRegionCheckboxChange(this)">
                <span>${reg}</span>
              </label>
            `;
          });
          container.innerHTML = html;
        }

        function toggleAllRegionsCheckbox(checked) {
          const meta = getHierarchyMeta();
          if (checked) {
            selectedUserRegions = new Set(['ALL']);
          } else {
            selectedUserRegions.clear();
          }
          renderRegionCheckboxes(meta);
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function onSingleRegionCheckboxChange(cb) {
          const meta = getHierarchyMeta();
          selectedUserRegions.delete('ALL');
          if (cb.checked) {
            selectedUserRegions.add(cb.value);
          } else {
            selectedUserRegions.delete(cb.value);
          }
          renderRegionCheckboxes(meta);
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function renderCenterCheckboxes(meta) {
          const container = document.getElementById('center-checkbox-list');
          if (!container) return;

          const isAllChecked = selectedUserCenters.has('ALL');

          let html = `
            <label style="display:flex; align-items:center; gap:8px; padding:4px 0; font-size:0.82rem; cursor:pointer; font-weight:700; border-bottom:1px solid #f1f5f9; margin-bottom:6px;">
              <input type="checkbox" id="cb-all-centers" value="ALL" ${isAllChecked ? 'checked' : ''} onchange="toggleAllCentersCheckbox(this.checked)">
              🌐 ALL Centers (All Network Coverage)
            </label>
          `;

          // Determine which regions to show
          let regionsToShow = meta.regions;
          if (!selectedUserRegions.has('ALL') && selectedUserRegions.size > 0) {
            regionsToShow = meta.regions.filter(r => selectedUserRegions.has(r));
          }

          regionsToShow.forEach(reg => {
            const centers = meta.centersByRegion[reg] ? Array.from(meta.centersByRegion[reg]).sort() : [];
            if (centers.length === 0) return;

            html += `
              <div class="center-region-group" data-region="${reg}" style="margin-top:6px; margin-bottom:4px;">
                <div style="display:flex; justify-content:space-between; align-items:center; background:#f8fafc; padding:3px 6px; border-radius:4px; font-size:0.75rem; font-weight:700; color:#334155; margin-bottom:4px;">
                  <span>📍 ${reg} (${centers.length})</span>
                  <button type="button" onclick="toggleRegionGroupCenters('${reg}')" style="background:none; border:none; color:#0284c7; font-size:0.7rem; cursor:pointer; font-weight:600;">Toggle</button>
                </div>
            `;

            centers.forEach(c => {
              const checked = isAllChecked || selectedUserCenters.has(c);
              html += `
                <label class="center-item-label" data-center="${c.toLowerCase()}" style="display:flex; align-items:center; gap:8px; padding:3px 8px; font-size:0.82rem; cursor:pointer;">
                  <input type="checkbox" class="user-center-cb" data-region="${reg}" value="${c}" ${checked ? 'checked' : ''} onchange="onSingleCenterCheckboxChange(this)">
                  <span>${c}</span>
                </label>
              `;
            });

            html += `</div>`;
          });

          container.innerHTML = html;
        }

        function toggleAllCentersCheckbox(checked) {
          if (checked) {
            selectedUserCenters = new Set(['ALL']);
          } else {
            selectedUserCenters.clear();
          }
          const meta = getHierarchyMeta();
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function onSingleCenterCheckboxChange(cb) {
          selectedUserCenters.delete('ALL');
          const meta = getHierarchyMeta();

          if (cb.checked) {
            selectedUserCenters.add(cb.value);
            // Auto add region if not present
            const reg = cb.dataset.region || meta.centerToRegion[cb.value];
            if (reg && !selectedUserRegions.has('ALL')) {
              selectedUserRegions.add(reg);
              renderRegionCheckboxes(meta);
            }
          } else {
            selectedUserCenters.delete(cb.value);
          }

          const metaNow = getHierarchyMeta();
          renderCenterCheckboxes(metaNow);
          updateUserPickerLabelsAndBadges();
        }

        function toggleRegionGroupCenters(reg) {
          const meta = getHierarchyMeta();
          const centers = meta.centersByRegion[reg] ? Array.from(meta.centersByRegion[reg]) : [];
          if (centers.length === 0) return;

          selectedUserCenters.delete('ALL');
          const allInRegSelected = centers.every(c => selectedUserCenters.has(c));
          
          if (allInRegSelected) {
            centers.forEach(c => selectedUserCenters.delete(c));
          } else {
            centers.forEach(c => selectedUserCenters.add(c));
            if (!selectedUserRegions.has('ALL')) {
              selectedUserRegions.add(reg);
              renderRegionCheckboxes(meta);
            }
          }

          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function selectAllCenters(check) {
          toggleAllCentersCheckbox(check);
        }

        function filterCenterChecklist(q) {
          const filter = (q || '').trim().toLowerCase();
          const items = document.querySelectorAll('.center-item-label');
          items.forEach(el => {
            const name = el.getAttribute('data-center') || '';
            el.style.display = name.includes(filter) ? 'flex' : 'none';
          });
        }

        function updateUserPickerLabelsAndBadges() {
          const rLabel = document.getElementById('region-picker-label');
          const cLabel = document.getElementById('center-picker-label');
          const tagsBar = document.getElementById('selected-centers-tags-bar');

          // Region Label
          if (rLabel) {
            if (selectedUserRegions.has('ALL')) {
              rLabel.innerText = '🌐 ALL Regions';
            } else if (selectedUserRegions.size === 0) {
              rLabel.innerHTML = '<span style="color:#94a3b8; font-weight:normal;">-- Select Region(s) --</span>';
            } else if (selectedUserRegions.size === 1) {
              rLabel.innerText = Array.from(selectedUserRegions)[0];
            } else {
              rLabel.innerText = `${selectedUserRegions.size} Regions Selected`;
            }
          }

          // Center Label
          if (cLabel) {
            if (selectedUserCenters.has('ALL')) {
              cLabel.innerText = '🌐 ALL (All Centers in Network)';
            } else if (selectedUserCenters.size === 0) {
              cLabel.innerHTML = '<span style="color:#94a3b8; font-weight:normal;">-- Select Center(s) --</span>';
            } else if (selectedUserCenters.size === 1) {
              cLabel.innerText = Array.from(selectedUserCenters)[0];
            } else {
              cLabel.innerText = `${selectedUserCenters.size} Centers Assigned (ACSO Charge)`;
            }
          }

          // Render Tags Ribbon
          if (tagsBar) {
            tagsBar.innerHTML = '';
            if (selectedUserCenters.has('ALL')) {
              tagsBar.innerHTML = '<span class="tag" style="background:#059669; padding:4px 10px; border-radius:14px;">🌐 ALL (Full Network Charge)</span>';
            } else if (selectedUserCenters.size === 0) {
              tagsBar.innerHTML = '<span style="color:#94a3b8; font-size:0.78rem; font-style:italic;">No centers selected yet. Please select from the Assigned Center(s) dropdown above.</span>';
            } else {
              selectedUserCenters.forEach(c => {
                const badge = document.createElement('span');
                badge.className = 'tag';
                badge.style.cssText = 'background:#0284c7; padding:3px 9px; border-radius:12px; display:inline-flex; align-items:center; gap:6px; font-size:0.75rem;';
                badge.innerHTML = `<span>${c}</span> <span onclick="removeSelectedCenterTag('${c}')" style="cursor:pointer; font-weight:bold; font-size:0.85rem;" title="Remove">&times;</span>`;
                tagsBar.appendChild(badge);
              });
              if (selectedUserCenters.size > 1) {
                const countBadge = document.createElement('span');
                countBadge.style.cssText = 'font-size:0.75rem; color:#64748b; font-weight:700; align-self:center;';
                countBadge.innerText = `(${selectedUserCenters.size} Centers in Charge)`;
                tagsBar.appendChild(countBadge);
              }
            }
          }
        }

        function removeSelectedCenterTag(c) {
          selectedUserCenters.delete(c);
          const meta = getHierarchyMeta();
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function filterAndRenderFeed() {
          const isSuperAdmin = (currentAdmin && currentAdmin.role === 'super_admin');
          const thMaster = document.getElementById('th-feed-select-all');
          if (thMaster) thMaster.style.display = isSuperAdmin ? 'table-cell' : 'none';

          if (selectedFeedRegions.size === 0 || selectedFeedCenters.size === 0) {
            selectedFeedRecordUuids.clear();
            updateFeedSelectionUI();
            document.getElementById('feed-stat-records').innerText = '0';
            document.getElementById('feed-stat-customers').innerText = '0';
            document.getElementById('feed-stat-enclosures').innerText = '0';
            document.getElementById('feed-stat-centers').innerText = '0';

            const tbody = document.getElementById('table-body');
            if (tbody) {
              tbody.innerHTML = `
                <tr>
                  <td colspan="16" style="text-align:center; padding:45px 20px; color:#64748b; font-size:0.95rem;">
                    <div style="font-size:2.2rem; margin-bottom:8px;">📍</div>
                    <div style="font-weight:700; color:#334155; margin-bottom:4px; font-size:1.05rem;">Please Select Region(s) and Center(s)</div>
                    <div style="color:#64748b; font-size:0.85rem; margin-bottom:8px;">Select at least one <strong>Region</strong> and <strong>Center</strong> above to view and manage captured survey records.</div>
                    <div style="font-size:0.8rem; color:#475569; background:#f1f5f9; display:inline-block; padding:5px 12px; border-radius:6px; border:1px solid #e2e8f0;">
                      💡 Note: Region & Center filters are unselected by default. Your survey records remain safely stored in the database.
                    </div>
                  </td>
                </tr>
              `;
            }
            return;
          }

          const q = (document.getElementById('feed-search').value || '').toLowerCase().trim();

          let filtered = cachedRecords;

          // Optional client-side RT room filter if specific RT rooms are checked
          if (selectedFeedRTRooms.size > 0) {
            const rtSet = new Set(Array.from(selectedFeedRTRooms).map(s => s.toLowerCase()));
            filtered = filtered.filter(r => r.rt_room && rtSet.has(r.rt_room.trim().toLowerCase()));
          }

          if (q) {
            filtered = filtered.filter(r => 
              (r.enclosure_id || '').toLowerCase().includes(q) ||
              (r.splitter_id || '').toLowerCase().includes(q) ||
              (r.kseb_post_number || '').toLowerCase().includes(q) ||
              (r.landmark || '').toLowerCase().includes(q) ||
              (r.surveyor_name || '').toLowerCase().includes(q) ||
              (r.surveyor_username || '').toLowerCase().includes(q) ||
              (r.center || '').toLowerCase().includes(q) ||
              (r.rt_room || '').toLowerCase().includes(q) ||
              (r.olt_name || '').toLowerCase().includes(q) ||
              (r.adl_subscriber_id || '').toLowerCase().includes(q) ||
              (r.acs_subscriber_id || '').toLowerCase().includes(q)
            );
          }

          // Compute stats
          let custTotal = 0;
          const encSet = new Set();
          const centerSet = new Set();

          filtered.forEach(r => {
            custTotal += (r.customers_connected || 0);
            if (r.enclosure_id) encSet.add(r.enclosure_id);
            if (r.center) centerSet.add(r.center);
          });

          document.getElementById('feed-stat-records').innerText = filtered.length;
          document.getElementById('feed-stat-customers').innerText = custTotal;
          document.getElementById('feed-stat-enclosures').innerText = encSet.size;
          document.getElementById('feed-stat-centers').innerText = centerSet.size;

          const tbody = document.getElementById('table-body');
          tbody.innerHTML = '';

          if (filtered.length === 0) {
            tbody.innerHTML = '<tr><td colspan="16" style="text-align:center; padding:30px; color:#94a3b8;"><div style="font-size:1.8rem; margin-bottom:6px;">🔍</div>No survey records match current filters.</td></tr>';
            updateFeedSelectionUI();
            return;
          }

          filtered.forEach((r, idx) => {
            const tr = document.createElement('tr');
            const timeDisplay = (r.survey_date_time || r.created_at || r.synced_at || '').slice(0, 19).replace('T', ' ');
            
            let checkboxHtml = '';
            let actionHtml = '';
            if (isSuperAdmin) {
              const isChecked = selectedFeedRecordUuids.has(String(r.client_uuid));
              checkboxHtml = `<td style="text-align:center; width:36px;">
                <input type="checkbox" class="feed-record-cb" value="${escapeHtml(r.client_uuid)}" ${isChecked ? 'checked' : ''} onchange="onSingleFeedRecordCheckboxChanged(this)" style="cursor:pointer; width:16px; height:16px;">
              </td>`;
              actionHtml = `<td style="text-align:center; white-space:nowrap;">
                <button class="btn btn-outline" style="padding:3px 7px; font-size:0.75rem; margin-right:4px; color:#0284c7; border-color:#0284c7;" onclick="openEditSurveyRecordModal('${r.client_uuid}')" title="Edit Record">✏️</button>
                <button class="btn btn-danger" style="padding:3px 7px; font-size:0.75rem;" onclick="deleteSurveyRecord('${r.client_uuid}')" title="Delete Record">🗑️</button>
              </td>`;
            } else {
              checkboxHtml = `<td style="display:none;"></td>`;
              actionHtml = `<td style="display:none;"></td>`;
            }

            const splitterBadge = r.splitter_id ? `<span style="background:#e0f2fe; color:#0369a1; padding:2px 5px; border-radius:4px; font-weight:700; font-size:0.75rem; margin-left:4px;">${escapeHtml(r.splitter_id)}</span>` : '';

            tr.innerHTML = `
              ${checkboxHtml}
              <td style="color:#64748b; font-family:monospace; font-size:0.8rem;">${idx + 1}</td>
              <td style="font-family:monospace; font-size:0.8rem; color:#64748b;">${timeDisplay || '-'}</td>
              <td><strong style="color:#0284c7;">${r.enclosure_id || '-'}</strong>${splitterBadge}</td>
              <td><span style="background:#f1f5f9; color:#475569; padding:2px 6px; border-radius:4px; font-size:0.75rem; font-weight:600;">${r.region || 'Thrissur'}</span></td>
              <td><strong>${r.center || '-'}</strong> / ${r.rt_room || '-'}</td>
              <td>${r.olt_name || '-'} [${r.port_number || '-'}]</td>
              <td><strong>${r.kseb_post_number || '-'}</strong></td>
              <td>${r.landmark || '-'}</td>
              <td><span style="font-family:monospace; font-size:0.8rem;">${r.lat_long || '-'}</span></td>
              <td><span class="tag">${r.customers_connected || 0}</span></td>
              <td><span style="background:#e0f2fe; color:#0369a1; padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.75rem;">${r.splitter_lead_color || '-'}</span></td>
              <td style="font-family:monospace; font-size:0.8rem;">${r.adl_subscriber_id || '-'}</td>
              <td style="font-family:monospace; font-size:0.8rem;">${r.acs_subscriber_id || '-'}</td>
              <td><strong style="color:#0284c7;">${r.surveyor_name || r.surveyor_username || 'App'}</strong></td>
              ${actionHtml}
            `;
            tbody.appendChild(tr);
          });
          updateFeedSelectionUI();
        }

        function downloadSelectedCenterExcel() {
          const regions = Array.from(selectedFeedRegions);
          const centers = Array.from(selectedFeedCenters);
          const rtRooms = Array.from(selectedFeedRTRooms);

          if (regions.length === 0 || centers.length === 0) {
            alert('Please select at least one Region and Center first to download the Excel spreadsheet.');
            return;
          }

          const params = [];
          if (regions.length > 0) params.push(`regions=${encodeURIComponent(regions.join(','))}`);
          if (centers.length > 0) params.push(`centers=${encodeURIComponent(centers.join(','))}`);
          if (rtRooms.length > 0) params.push(`rt_rooms=${encodeURIComponent(rtRooms.join(','))}`);

          downloadWithAuth(`/api/export-center-excel?${params.join('&')}`);
        }

        function openEditSurveyRecordModal(uuid) {
          console.log('[openEditSurveyRecordModal] Opening for uuid:', uuid);
          const r = cachedRecords.find(item => String(item.client_uuid) === String(uuid));
          if (!r) {
            console.error('[openEditSurveyRecordModal] Record not found for uuid:', uuid, cachedRecords);
            alert('Record not found.');
            return;
          }
          const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.value = val !== undefined && val !== null ? val : '';
          };
          const setText = (id, txt) => {
            const el = document.getElementById(id);
            if (el) el.innerText = txt !== undefined && txt !== null ? txt : '-';
          };

          setVal('edit-rec-uuid', r.client_uuid);
          setText('edit-rec-enclosure-id', r.enclosure_id || '-');
          setText('edit-rec-splitter-id', `${r.splitter_id || '-'} (${r.splitter_ratio || '-'})`);
          setText('edit-rec-center-rt', `${r.center || '-'} / ${r.rt_room || '-'}`);
          setText('edit-rec-node-port', `${r.olt_name || '-'} [${r.port_number || '-'}]`);

          setVal('edit-rec-post', r.kseb_post_number || '');
          setVal('edit-rec-landmark', r.landmark || '');
          setVal('edit-rec-coords', r.lat_long || '');
          setVal('edit-rec-cust', (r.customers_connected !== undefined) ? r.customers_connected : 0);
          setVal('edit-rec-ratio', r.splitter_ratio || '1:8');
          setVal('edit-rec-color', r.splitter_lead_color || '');
          setVal('edit-rec-adl', r.adl_subscriber_id || '');
          setVal('edit-rec-acs', r.acs_subscriber_id || '');
          setVal('edit-rec-port', r.port_number || '');
          setVal('edit-rec-enc', r.enclosure_number || '');
          setVal('edit-rec-splitter', r.splitter_id || '');

          const modal = document.getElementById('edit-survey-modal');
          if (modal) {
            modal.style.display = 'flex';
          } else {
            console.error('[openEditSurveyRecordModal] edit-survey-modal element not found in DOM!');
          }
        }

        function closeEditSurveyRecordModal() {
          const modal = document.getElementById('edit-survey-modal');
          if (modal) modal.style.display = 'none';
        }

        async function saveEditedSurveyRecord(e) {
          e.preventDefault();
          const uuid = document.getElementById('edit-rec-uuid').value;
          if (!uuid) return;

          const payload = {
            kseb_post_number: document.getElementById('edit-rec-post').value.trim(),
            landmark: document.getElementById('edit-rec-landmark').value.trim(),
            lat_long: document.getElementById('edit-rec-coords').value.trim(),
            customers_connected: parseInt(document.getElementById('edit-rec-cust').value, 10) || 0,
            splitter_ratio: document.getElementById('edit-rec-ratio').value,
            splitter_lead_color: document.getElementById('edit-rec-color').value,
            adl_subscriber_id: document.getElementById('edit-rec-adl').value.trim(),
            acs_subscriber_id: document.getElementById('edit-rec-acs').value.trim(),
            port_number: document.getElementById('edit-rec-port').value.trim(),
            enclosure_number: document.getElementById('edit-rec-enc').value.trim(),
            splitter_id: document.getElementById('edit-rec-splitter').value.trim()
          };

          try {
            const res = await authFetch(`/api/records/${uuid}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            if (res.ok) {
              closeEditSurveyRecordModal();
              fetchData();
              alert('✅ Survey record updated successfully!');
            } else {
              const err = await res.json().catch(() => ({}));
              alert('Error updating record: ' + (err.detail || 'Server error'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteSurveyRecord(uuid) {
          if (!currentAdmin || currentAdmin.role !== 'super_admin') {
            alert('Permission Denied: Only Super Admin can delete records.');
            return;
          }
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Record Deletion',
            'Are you sure you want to permanently delete this survey record from the database?',
            'Delete Record',
            '#dc2626'
          );
          if (!confirmed) return;

          try {
            const res = await authFetch(`/api/records/${uuid}`, { method: 'DELETE' });
            if (res.ok) {
              selectedFeedRecordUuids.delete(String(uuid));
              updateFeedSelectionUI();
              fetchData();
              if (typeof fetchFoldersSummary === 'function') {
                fetchFoldersSummary();
              }
            } else {
              const err = await res.json();
              alert('Error: ' + (err.detail || 'Could not delete record'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteSelectedSurveyRecords() {
          if (!currentAdmin || currentAdmin.role !== 'super_admin') {
            alert('Permission Denied: Only Super Admin can delete records.');
            return;
          }
          const count = selectedFeedRecordUuids.size;
          if (count === 0) return;

          const confirmed = await showConfirmModal(
            '🗑️ Confirm Bulk Record Deletion',
            `Are you sure you want to permanently delete ${count} selected survey record(s) from the database? This action cannot be undone.`,
            `Delete ${count} Records`,
            '#dc2626'
          );
          if (!confirmed) return;

          const uuids = Array.from(selectedFeedRecordUuids);
          try {
            const res = await authFetch('/api/records/bulk-delete', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ uuids: uuids })
            });
            const data = await res.json();
            if (res.ok) {
              selectedFeedRecordUuids.clear();
              alert(`✅ ${data.message || 'Records deleted successfully.'}`);
              fetchData();
              if (typeof fetchFoldersSummary === 'function') {
                fetchFoldersSummary();
              }
            } else {
              alert('Error: ' + (data.detail || 'Could not delete records'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        // Tab 2: User Access Management (4 Tiers)
        let cachedUsersList = [];

        async function fetchUsers() {
          const tbody = document.getElementById('users-table-body');
          try {
            const res = await authFetch('/api/users');
            if (!res.ok) {
              const err = await res.json().catch(() => ({}));
              tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:20px; color:#dc2626; font-weight:600;">⚠️ Failed to load user credentials: ${err.detail || res.statusText} (${res.status}). <button class="btn btn-danger" style="margin-left:10px; padding:4px 10px; font-size:0.75rem;" onclick="adminLogout()">Sign In Again</button></td></tr>`;
              return;
            }
            const data = await res.json();
            cachedUsersList = (data && data.users) ? data.users : [];
            tbody.innerHTML = '';

            if (cachedUsersList.length === 0) {
              tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:20px; color:#64748b;">No users found in database. Click "+ Add User" above to create one.</td></tr>';
              return;
            }

            const roleBadges = {
              'super_admin': '<span class="role-tag-super_admin">👑 Super Admin</span>',
              'rcsm': '<span class="role-tag-rcsm">📊 RCSM</span>',
              'acso': '<span class="role-tag-acso">📝 ACSO</span>',
              'field_technician': '<span class="role-tag-field_technician">👷 Field Tech</span>'
            };

            cachedUsersList.forEach((u, idx) => {
              const tr = document.createElement('tr');
              const roleClean = normalizeAdminRole(u.role);
              const badgeHtml = roleBadges[roleClean] || `<span class="tag">${escapeHtml(u.role)}</span>`;
              const emailDisplay = u.email ? `<span style="font-family:monospace; font-size:0.8rem; color:#0369a1;">${escapeHtml(u.email)}</span>` : '<span style="color:#94a3b8; font-style:italic; font-size:0.78rem;">No Email</span>';
              
              tr.innerHTML = `
                <td><strong>${escapeHtml(u.username)}</strong></td>
                <td>${escapeHtml(u.full_name || u.username)}</td>
                <td>${emailDisplay}</td>
                <td><span style="background:#f1f5f9; padding:2px 8px; border-radius:4px; font-weight:600; font-size:0.8rem;">${escapeHtml(u.assigned_region || 'Thrissur')}</span></td>
                <td>
                  ${(() => {
                    const centers = (u.assigned_center || 'ALL').split(',').map(s => s.trim()).filter(Boolean);
                    if (centers.length === 0 || centers.includes('ALL')) {
                      return '<span class="tag" style="background:#059669;">ALL (Network)</span>';
                    }
                    if (centers.length === 1) {
                      return `<span class="tag" style="background:#0284c7;">${escapeHtml(centers[0])}</span>`;
                    }
                    if (centers.length <= 2) {
                      return centers.map(c => `<span class="tag" style="background:#0284c7; margin-right:3px;">${escapeHtml(c)}</span>`).join('');
                    }
                    return `<span class="tag" style="background:#0284c7;" title="${escapeHtml(centers.join(', '))}">🏢 ${centers.length} Centers Charge</span>`;
                  })()}
                </td>
                <td>${badgeHtml}</td>
                <td style="color:#64748b; font-size:0.8rem; font-family:monospace;">${escapeHtml((u.created_at || '').slice(0, 19).replace('T', ' '))}</td>
                <td style="text-align:center; white-space:nowrap;">
                  <button class="btn btn-outline" style="padding:3px 8px; font-size:0.75rem; margin-right:4px;" onclick="editUserByIndex(${idx})">✏️ Edit</button>
                  ${u.username !== 'admin' ? `<button class="btn btn-danger" style="padding:3px 8px; font-size:0.75rem;" onclick="deleteUserByIndex(${idx})">🗑️</button>` : '<span style="color:#94a3b8; font-size:0.75rem;">Root</span>'}
                </td>
              `;
              tbody.appendChild(tr);
            });
          } catch(e) {
            console.error('Error in fetchUsers:', e);
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:20px; color:#dc2626;">Error loading user list: ${escapeHtml(e.message)}</td></tr>`;
          }
        }

        function editUserByIndex(idx) {
          const u = cachedUsersList[idx];
          if (!u) return;
          openEditUserModal(u.username, u.full_name, u.email || '', u.assigned_center || 'ALL', u.assigned_region || 'Thrissur', normalizeAdminRole(u.role));
        }

        function deleteUserByIndex(idx) {
          const u = cachedUsersList[idx];
          if (!u) return;
          deleteUser(u.username);
        }

        async function downloadUsersBackup() {
          try {
            const res = await authFetch('/api/config/users');
            if (!res.ok) {
              alert('Failed to download users backup. Error code: ' + res.status);
              return;
            }
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'users_config.json';
            document.body.appendChild(a);
            a.click();
            a.remove();
          } catch(e) {
            alert('Download failed: ' + e.message);
          }
        }

        async function testSmtpConnection() {
          const testEmail = prompt("Enter an email address to send an SMTP test message to:", "");
          if (testEmail === null) return;
          const target = testEmail.trim();
          try {
            const res = await authFetch('/api/test-smtp', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ recipient_email: target })
            });
            const data = await res.json();
            if (data.status === 'success') {
              alert('✅ ' + data.message);
            } else if (data.status === 'warning') {
              alert('⚠️ ' + data.message);
            } else {
              alert('❌ ' + (data.message || data.detail || 'SMTP test failed'));
            }
          } catch(err) {
            alert('❌ Network error testing SMTP: ' + err.message);
          }
        }

        function openAddUserModal() {
          document.getElementById('user-modal-title').innerHTML = '➕ Add New User';
          document.getElementById('modal-new-user').value = '';
          document.getElementById('modal-new-user').removeAttribute('readonly');
          document.getElementById('modal-new-user').style.background = '#ffffff';
          document.getElementById('modal-new-pass').value = '';
          document.getElementById('modal-new-pass').placeholder = 'PIN / Password';
          document.getElementById('modal-new-pass').setAttribute('required', 'true');
          const passReq = document.getElementById('modal-pass-required');
          if (passReq) passReq.style.display = 'inline';
          document.getElementById('modal-new-name').value = '';
          document.getElementById('modal-new-email').value = '';
          document.getElementById('modal-new-role').value = 'field_technician';
          document.getElementById('btn-modal-save-user').innerHTML = '💾 Save User';

          populateUserRegionAndCenterDropdowns('', '');
          document.getElementById('user-modal').style.display = 'flex';
        }

        function openEditUserModal(username, fullName, email, center, region, role) {
          document.getElementById('user-modal-title').innerHTML = `✏️ Edit User: ${escapeHtml(username)}`;
          document.getElementById('modal-new-user').value = username;
          document.getElementById('modal-new-user').setAttribute('readonly', 'true');
          document.getElementById('modal-new-user').style.background = '#f1f5f9';
          document.getElementById('modal-new-pass').value = '';
          document.getElementById('modal-new-pass').placeholder = '(Leave empty to keep existing password)';
          document.getElementById('modal-new-pass').removeAttribute('required');
          const passReq = document.getElementById('modal-pass-required');
          if (passReq) passReq.style.display = 'none';
          document.getElementById('modal-new-name').value = fullName || username;
          document.getElementById('modal-new-email').value = email || '';

          populateUserRegionAndCenterDropdowns(region || 'Thrissur', center || 'ALL');

          document.getElementById('modal-new-role').value = role || 'field_technician';
          document.getElementById('btn-modal-save-user').innerHTML = '💾 Update User';
          document.getElementById('user-modal').style.display = 'flex';
        }

        function closeUserModal() {
          document.getElementById('user-modal').style.display = 'none';
          const rp = document.getElementById('region-dropdown-panel');
          if (rp) rp.style.display = 'none';
          const cp = document.getElementById('center-dropdown-panel');
          if (cp) cp.style.display = 'none';
        }

        async function saveUserFromModal(e) {
          e.preventDefault();
          
          let regionsArr = Array.from(selectedUserRegions);
          const centersArr = Array.from(selectedUserCenters);

          if (centersArr.length === 0) {
            alert('Please select at least one assigned center (or choose ALL).');
            return;
          }

          // Auto-derive regions from selected centers if no region was explicitly checked
          if (regionsArr.length === 0) {
            if (centersArr.includes('ALL')) {
              regionsArr = ['ALL'];
            } else {
              const meta = getHierarchyMeta();
              const derived = new Set();
              centersArr.forEach(c => {
                const r = meta.centerToRegion[c];
                if (r) derived.add(r);
              });
              regionsArr = Array.from(derived);
            }
          }
          if (regionsArr.length === 0) regionsArr = ['Thrissur'];

          const u = {
            username: document.getElementById('modal-new-user').value.trim(),
            password: document.getElementById('modal-new-pass').value.trim(),
            full_name: document.getElementById('modal-new-name').value.trim(),
            email: document.getElementById('modal-new-email').value.trim(),
            assigned_region: regionsArr.includes('ALL') ? 'ALL' : regionsArr.join(', '),
            assigned_center: centersArr.includes('ALL') ? 'ALL' : centersArr.join(', '),
            role: document.getElementById('modal-new-role').value
          };

          try {
            const res = await authFetch('/api/users', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(u)
            });
            const data = await res.json();
            if (res.ok) {
              alert(data.message || 'User saved successfully!');
              closeUserModal();
              fetchUsers();
            } else {
              alert('Error: ' + (data.detail || 'Could not save user'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteUser(u) {
          const confirmed = await showConfirmModal(
            '🗑️ Confirm User Deletion',
            `Are you sure you want to delete user account "${u}"?`,
            'Confirm Delete',
            '#dc2626'
          );
          if (!confirmed) return;
          await authFetch('/api/users/' + u, { method: 'DELETE' });
          fetchUsers();
        }

        async function importUsersConfig(e) {
          const file = e.target.files[0];
          if (!file) return;
          const formData = new FormData();
          formData.append('file', file);
          try {
            const res = await authFetch('/api/config/users', { method: 'POST', body: formData });
            const data = await res.json();
            alert(data.message || 'Users restored successfully!');
            fetchUsers();
          } catch (err) {
            alert('Error restoring users config: ' + err);
          }
          e.target.value = '';
        }

        // Tab 3: Network Hierarchy
        async function fetchHierarchy() {
          try {
            const res = await authFetch(`/api/hierarchy?t=${Date.now()}`);
            const data = await res.json();
            const hier = (data && data.hierarchy) ? data.hierarchy : data;
            cachedHierarchy = hier;
            const tbody = document.getElementById('hierarchy-table-body');
            tbody.innerHTML = '';

            let totalCenters = 0;
            let totalRTRooms = 0;
            let totalOLTs = 0;
            fullHierarchyRows = [];

            Object.keys(hier).sort().forEach(center => {
              const rts = hier[center];
              if (!rts || typeof rts !== 'object') return;
              let centerHasValidOlts = false;
              Object.keys(rts).sort().forEach(rtRoom => {
                const olts = rts[rtRoom];
                if (!olts || typeof olts !== 'object') return;
                const oltKeys = Object.keys(olts).sort();
                if (oltKeys.length === 0) return;

                totalRTRooms++;
                centerHasValidOlts = true;
                oltKeys.forEach(oltName => {
                  totalOLTs++;
                  const entry = olts[oltName];
                  let region = 'Thrissur';
                  let tech = 'GPON';
                  let oltType = '8 P';
                  let ports = [];

                  if (Array.isArray(entry)) {
                    ports = entry;
                    oltType = ports.length > 0 ? (ports.length + ' P') : '8 P';
                  } else if (entry && typeof entry === 'object') {
                    region = entry.region || 'Thrissur';
                    tech = entry.tech || entry.technology || 'GPON';
                    oltType = entry.olt_type || entry.oltType || (entry.ports ? entry.ports.length + ' P' : '8 P');
                    ports = entry.ports || (oltType.includes('16') ? Array.from({length:16}, (_, i) => 'P' + (i+1)) : Array.from({length:8}, (_, i) => 'P' + (i+1)));
                  }

                  fullHierarchyRows.push({
                    region,
                    center,
                    rtRoom,
                    tech,
                    oltName,
                    oltType,
                    portsCount: ports.length
                  });
                });
              });
              if (centerHasValidOlts) {
                totalCenters++;
              }
            });

            document.getElementById('hier-total-centers').innerText = totalCenters;
            document.getElementById('hier-total-rtrooms').innerText = totalRTRooms;
            document.getElementById('hier-total-olts').innerText = totalOLTs;

            renderHierarchyTable(fullHierarchyRows);
            updateFeedFilterDropdowns();
            populateUserRegionAndCenterDropdowns();
          } catch(e) {
            console.error(e);
          }
        }

        function renderHierarchyTable(rows) {
          const tbody = document.getElementById('hierarchy-table-body');
          tbody.innerHTML = '';
          const masterCb = document.getElementById('select-all-cb');
          if (masterCb) { masterCb.checked = false; masterCb.indeterminate = false; }
          updateSelectionUI();

          if (rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding:20px; color:#94a3b8;">No hierarchy records found. Upload an Excel file or click "+ Add Single Node" above.</td></tr>';
            return;
          }
          rows.forEach((r, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td style="text-align:center;"><input type="checkbox" class="row-cb" data-center="${encodeURIComponent(r.center)}" data-rt="${encodeURIComponent(r.rtRoom)}" data-olt="${encodeURIComponent(r.oltName)}" onchange="updateSelectionUI()"></td>
              <td style="color:#64748b; font-family:monospace; font-size:0.8rem;">${idx + 1}</td>
              <td><span style="background:#f1f5f9; color:#334155; padding:2px 8px; border-radius:4px; font-weight:600; font-size:0.8rem;">${r.region}</span></td>
              <td><strong>${r.center}</strong></td>
              <td>${r.rtRoom}</td>
              <td><span class="tag" style="background:#0284c7; color:white; font-size:0.75rem;">${r.tech}</span></td>
              <td><strong style="color:#0f172a;">${r.oltName}</strong></td>
              <td><span class="tag" style="background:#059669; color:white; font-size:0.75rem;">${r.oltType}</span></td>
              <td style="white-space:nowrap;">
                <button class="btn btn-outline" style="padding:3px 8px; font-size:0.75rem; margin-right:4px;" onclick="openEditOltModal('${encodeURIComponent(r.region)}', '${encodeURIComponent(r.center)}', '${encodeURIComponent(r.rtRoom)}', '${encodeURIComponent(r.tech)}', '${encodeURIComponent(r.oltName)}', '${encodeURIComponent(r.oltType)}')">✏️ Edit</button>
                <button class="btn btn-danger" style="padding:3px 8px; font-size:0.75rem;" onclick="deleteOlt('${encodeURIComponent(r.center)}', '${encodeURIComponent(r.rtRoom)}', '${encodeURIComponent(r.oltName)}')">🗑️</button>
              </td>
            `;
            tbody.appendChild(tr);
          });
        }

        function toggleSelectAll(masterCb) {
          const checkboxes = document.querySelectorAll('#hierarchy-table-body .row-cb');
          checkboxes.forEach(cb => { cb.checked = masterCb.checked; });
          updateSelectionUI();
        }

        function updateSelectionUI() {
          const allCbs = document.querySelectorAll('#hierarchy-table-body .row-cb');
          const checked = document.querySelectorAll('#hierarchy-table-body .row-cb:checked');
          const countEl = document.getElementById('selection-count');
          const btnEl = document.getElementById('btn-delete-selected');
          const masterCb = document.getElementById('select-all-cb');

          if (countEl && btnEl) {
            if (checked.length > 0) {
              countEl.innerText = `${checked.length} selected`;
              countEl.style.display = '';
              btnEl.style.display = '';
              btnEl.innerText = `🗑️ Delete Selected (${checked.length})`;
            } else {
              countEl.style.display = 'none';
              btnEl.style.display = 'none';
            }
          }

          if (masterCb) {
            if (allCbs.length > 0 && checked.length === allCbs.length) {
              masterCb.checked = true;
              masterCb.indeterminate = false;
            } else if (checked.length > 0) {
              masterCb.checked = false;
              masterCb.indeterminate = true;
            } else {
              masterCb.checked = false;
              masterCb.indeterminate = false;
            }
          }
        }

        function filterHierarchyTable() {
          const q = (document.getElementById('hierarchy-search').value || '').toLowerCase().trim();
          if (!q) {
            renderHierarchyTable(fullHierarchyRows);
            return;
          }
          const filtered = fullHierarchyRows.filter(r => 
            (r.region || '').toLowerCase().includes(q) ||
            (r.center || '').toLowerCase().includes(q) || 
            (r.rtRoom || '').toLowerCase().includes(q) || 
            (r.tech || '').toLowerCase().includes(q) ||
            (r.oltName || '').toLowerCase().includes(q) ||
            (r.oltType || '').toLowerCase().includes(q)
          );
          renderHierarchyTable(filtered);
        }

        function openAddOltModal() {
          document.getElementById('olt-modal-title').innerText = '➕ Add New Node';
          document.getElementById('modal-old-center').value = '';
          document.getElementById('modal-old-rtroom').value = '';
          document.getElementById('modal-old-olt').value = '';
          document.getElementById('modal-region').value = 'Thrissur';
          document.getElementById('modal-center').value = '';
          document.getElementById('modal-rtroom').value = '';
          document.getElementById('modal-tech').value = 'GPON';
          document.getElementById('modal-olt').value = '';
          document.getElementById('modal-ports').value = '8 P';
          document.getElementById('olt-modal').style.display = 'flex';
        }

        function openEditOltModal(encReg, encC, encRt, encTech, encOlt, encType) {
          const reg = decodeURIComponent(encReg || 'Thrissur');
          const c = decodeURIComponent(encC);
          const rt = decodeURIComponent(encRt);
          const tech = decodeURIComponent(encTech || 'GPON');
          const olt = decodeURIComponent(encOlt);
          const oType = decodeURIComponent(encType || '8 P');

          document.getElementById('olt-modal-title').innerText = '✏️ Edit Node';
          document.getElementById('modal-old-center').value = c;
          document.getElementById('modal-old-rtroom').value = rt;
          document.getElementById('modal-old-olt').value = olt;
          document.getElementById('modal-region').value = reg;
          document.getElementById('modal-center').value = c;
          document.getElementById('modal-rtroom').value = rt;
          document.getElementById('modal-tech').value = tech;
          document.getElementById('modal-olt').value = olt;
          document.getElementById('modal-ports').value = oType.includes('16') ? '16 P' : (oType.includes('32') ? '32 P' : '8 P');
          document.getElementById('olt-modal').style.display = 'flex';
        }

        function closeOltModal() {
          document.getElementById('olt-modal').style.display = 'none';
        }

        async function saveOltModal(e) {
          e.preventDefault();
          const isEdit = !!document.getElementById('modal-old-olt').value.trim();
          const confirmed = await showConfirmModal(
            isEdit ? '✏️ Confirm Save Edit' : '➕ Confirm Add Node',
            isEdit ? 'Are you sure you want to save changes to this node?' : 'Are you sure you want to add this new node?',
            'Confirm & Save',
            '#16a34a'
          );
          if (!confirmed) return;

          const oltTypeVal = document.getElementById('modal-ports').value;
          const portCnt = oltTypeVal.includes('16') ? 16 : (oltTypeVal.includes('32') ? 32 : 8);
          const payload = {
            region: document.getElementById('modal-region').value.trim() || 'Thrissur',
            center: document.getElementById('modal-center').value.trim(),
            rt_room: document.getElementById('modal-rtroom').value.trim(),
            tech: document.getElementById('modal-tech').value.trim() || 'GPON',
            olt_name: document.getElementById('modal-olt').value.trim(),
            olt_type: oltTypeVal,
            port_count: portCnt,
            old_center: document.getElementById('modal-old-center').value.trim() || null,
            old_rt_room: document.getElementById('modal-old-rtroom').value.trim() || null,
            old_olt_name: document.getElementById('modal-old-olt').value.trim() || null
          };
          try {
            const res = await authFetch('/api/hierarchy/olt', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (res.ok) {
              closeOltModal();
              fetchHierarchy();
            } else {
              alert('Error: ' + (data.detail || 'Could not save node'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteOlt(encC, encRt, encOlt) {
          const c = decodeURIComponent(encC);
          const rt = decodeURIComponent(encRt);
          const olt = decodeURIComponent(encOlt);
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Deletion',
            `Are you sure you want to delete node "${olt}" from ${c} (${rt})?`,
            'Confirm Delete',
            '#dc2626'
          );
          if (!confirmed) return;

          try {
            const res = await authFetch('/api/hierarchy/olt', {
              method: 'DELETE',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ center: c, rt_room: rt, olt_name: olt })
            });
            const data = await res.json();
            if (res.ok) {
              fetchHierarchy();
            } else {
              alert('Error: ' + (data.detail || 'Could not delete node'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteSelectedOlts() {
          const checked = document.querySelectorAll('#hierarchy-table-body .row-cb:checked');
          if (checked.length === 0) return;
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Bulk Deletion',
            `Are you sure you want to delete ${checked.length} selected node(s)?`,
            'Confirm Delete',
            '#dc2626'
          );
          if (!confirmed) return;

          const items = [];
          checked.forEach(cb => {
            items.push({
              center: decodeURIComponent(cb.dataset.center),
              rt_room: decodeURIComponent(cb.dataset.rt),
              olt_name: decodeURIComponent(cb.dataset.olt)
            });
          });

          try {
            const res = await authFetch('/api/hierarchy/bulk-delete', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ items: items })
            });
            const data = await res.json();
            if (res.ok) {
              alert(data.message || 'Deleted successfully');
              fetchHierarchy();
            } else {
              alert('Error: ' + (data.detail || 'Could not delete selected nodes'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function uploadHierarchyExcel(e) {
          const file = e.target.files[0];
          if (!file) return;

          const statusDiv = document.getElementById('hierarchy-upload-status');
          statusDiv.style.display = 'block';
          statusDiv.style.background = '#e0f2fe';
          statusDiv.style.color = '#0369a1';
          statusDiv.innerText = `⏳ Uploading and parsing ${file.name}...`;

          const formData = new FormData();
          formData.append('file', file);

          try {
            const res = await authFetch('/api/upload-hierarchy-excel', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (res.ok) {
              statusDiv.style.background = '#d1fae5';
              statusDiv.style.color = '#065f46';
              statusDiv.innerText = `✅ ${data.message || 'Hierarchy imported successfully!'}`;
              await fetchHierarchy();
            } else {
              statusDiv.style.background = '#fee2e2';
              statusDiv.style.color = '#991b1b';
              statusDiv.innerText = `❌ Error: ${data.detail || 'Upload failed'}`;
            }
          } catch(err) {
            statusDiv.style.background = '#fee2e2';
            statusDiv.style.color = '#991b1b';
            statusDiv.innerText = `❌ Network Error: ${err.message}`;
          } finally {
            e.target.value = '';
          }
        }

        async function clearAllHierarchy() {
          const confirmed = await showConfirmModal(
            '⚠️ Confirm Clear All Node Master Data',
            'Are you sure you want to completely clear ALL Centers, RT Rooms, and Nodes from the Node Master? This action cannot be undone.',
            'Clear Everything',
            '#dc2626'
          );
          if (!confirmed) return;

          try {
            const res = await authFetch('/api/hierarchy/clear', {
              method: 'DELETE'
            });
            const data = await res.json();
            if (res.ok) {
              alert('✅ ' + (data.message || 'All hierarchy data cleared.'));
              fetchHierarchy();
            } else {
              alert('Error: ' + (data.detail || 'Could not clear hierarchy data.'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function uploadSurveyExcel(e) {
          const file = e.target.files[0];
          if (!file) return;

          const confirmed = await showConfirmModal(
            '📤 Confirm Survey Data Import',
            `Import survey data records from "${file.name}" into the database? Records with matching IDs will be updated.`,
            'Import Now',
            '#0f766e'
          );
          if (!confirmed) {
            e.target.value = '';
            return;
          }

          const formData = new FormData();
          formData.append('file', file);

          try {
            const res = await authFetch('/api/upload-survey-excel', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (res.ok) {
              alert('✅ ' + (data.message || 'Survey records imported successfully!'));
              fetchData();
              fetchFoldersSummary();
            } else {
              alert('❌ Import failed: ' + (data.detail || 'Error uploading survey file'));
            }
          } catch(err) {
            alert('❌ Network error: ' + err.message);
          } finally {
            e.target.value = '';
          }
        }

        // Tab 4: Dynamic Region & Center Folders
        async function fetchFoldersSummary() {
          try {
            const res = await authFetch('/api/data-folders-summary');
            const data = await res.json();
            
            document.getElementById('folder-stat-regions').innerText = data.total_regions || 0;
            document.getElementById('folder-stat-centers').innerText = data.total_centers || 0;
            document.getElementById('folder-stat-records').innerText = data.total_records || 0;

            const container = document.getElementById('folders-container');
            container.innerHTML = '';

            const isSuperAdmin = (currentAdmin && currentAdmin.role === 'super_admin');
            const regions = data.regions || {};
            const regKeys = Object.keys(regions);

            if (regKeys.length === 0) {
              container.innerHTML = '<p style="text-align:center; padding:20px; color:#64748b;">No region or center data folders found.</p>';
              return;
            }

            regKeys.forEach(regName => {
              const centers = regions[regName] || [];
              const regCard = document.createElement('div');
              regCard.style.cssText = 'background:#f8fafc; border:1px solid #cbd5e1; border-radius:10px; padding:16px; margin-bottom:16px;';
              
              let tableRows = '';
              centers.forEach((c, idx) => {
                tableRows += `
                  <tr>
                    <td style="color:#64748b; font-family:monospace; font-size:0.8rem;">${idx + 1}</td>
                    <td><strong style="color:#0f172a;">${c.center}</strong></td>
                    <td style="font-family:monospace; font-size:0.8rem; color:#475569;">${c.folder}/</td>
                    <td style="font-family:monospace; font-size:0.8rem; color:#0284c7;">${c.excel_file}</td>
                    <td>
                      <span class="tag" style="background:${c.records_count > 0 ? '#059669' : '#94a3b8'};">
                        ${c.records_count} record${c.records_count === 1 ? '' : 's'}
                      </span>
                    </td>
                    <td style="white-space:nowrap;">
                      <button onclick="downloadWithAuth('/api/export-center-excel?center=${encodeURIComponent(c.center)}&region=${encodeURIComponent(c.region)}')" 
                              class="btn btn-outline" 
                              style="padding:4px 10px; font-size:0.75rem;">
                        📥 Download Excel
                      </button>
                      ${isSuperAdmin && c.records_count > 0 ? `
                        <button onclick="clearCenterSurveyRecords('${escapeHtml(c.region)}', '${escapeHtml(c.center)}', ${c.records_count})" 
                                class="btn btn-danger" 
                                style="padding:4px 9px; font-size:0.75rem; margin-left:6px;"
                                title="Permanently delete all survey records in this center">
                          🗑️ Clear Records (${c.records_count})
                        </button>
                      ` : ''}
                      ${isSuperAdmin && c.records_count === 0 ? `
                        <button onclick="deleteEntireCenterFolder('${escapeHtml(c.region)}', '${escapeHtml(c.center)}')" 
                                class="btn btn-outline" 
                                style="padding:4px 8px; font-size:0.75rem; color:#dc2626; border-color:#dc2626; margin-left:6px;" 
                                title="Remove empty center from hierarchy">
                          🗑️ Remove Folder
                        </button>
                      ` : ''}
                    </td>
                  </tr>
                `;
              });

              regCard.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                  <h4 style="margin:0; font-size:1.05rem; color:#1e293b;">
                    📍 Region: <span style="color:#2563eb;">${regName}</span> 
                    <span style="font-size:0.8rem; font-weight:normal; color:#64748b; margin-left:8px;">(${centers.length} Centers)</span>
                  </h4>
                  <span style="font-family:monospace; font-size:0.8rem; background:#e2e8f0; padding:4px 8px; border-radius:6px; color:#334155;">
                    data/${regName}/
                  </span>
                </div>
                <div style="overflow-x:auto;">
                  <table>
                    <thead>
                      <tr>
                        <th style="width:36px;">#</th>
                        <th>Center</th>
                        <th>Folder Path (in ZIP)</th>
                        <th>Excel Spreadsheet</th>
                        <th>Captured Records</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${tableRows}
                    </tbody>
                  </table>
                </div>
              `;
              container.appendChild(regCard);
            });
          } catch(err) {
            console.error('Failed fetching folders summary:', err);
          }
        }

        async function clearCenterSurveyRecords(reg, cent, count) {
          if (!currentAdmin || currentAdmin.role !== 'super_admin') {
            alert('Permission Denied: Only Super Admin can delete records.');
            return;
          }
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Clear Center Records',
            `Are you sure you want to permanently delete all ${count} survey record(s) for center "${cent}" (${reg}) from the database? This cannot be undone.`,
            `Delete ${count} Records`,
            '#dc2626'
          );
          if (!confirmed) return;

          try {
            const res = await authFetch('/api/records/clear-center', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ region: reg, center: cent })
            });
            const data = await res.json();
            if (res.ok) {
              alert(`✅ ${data.message || 'Records deleted successfully.'}`);
              fetchFoldersSummary();
              fetchData();
            } else {
              alert('Error: ' + (data.detail || 'Could not delete records'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function deleteEntireCenterFolder(reg, cent) {
          if (!currentAdmin || currentAdmin.role !== 'super_admin') {
            alert('Permission Denied: Only Super Admin can manage folders.');
            return;
          }
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Remove Center Folder',
            `Are you sure you want to remove empty center "${cent}" (${reg}) from network hierarchy and dynamic export folders?`,
            'Remove Center',
            '#dc2626'
          );
          if (!confirmed) return;

          try {
            const res = await authFetch('/api/hierarchy/center', {
              method: 'DELETE',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ center: cent })
            });
            const data = await res.json();
            if (res.ok) {
              alert(`✅ ${data.message || 'Center removed successfully.'}`);
              fetchFoldersSummary();
              fetchHierarchy();
            } else {
              alert('Error: ' + (data.detail || 'Could not remove center'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        async function resyncFoldersAction() {
          try {
            const res = await authFetch('/api/resync-data-folders', { method: 'POST' });
            const data = await res.json();
            alert(data.message || 'Storage optimized and counts refreshed!');
            fetchFoldersSummary();
          } catch(err) {
            alert('Failed to optimize: ' + err.message);
          }
        }

        // Confirmation Modal Helper
        function showConfirmModal(title, message, confirmBtnText = 'Confirm', confirmBtnColor = '#dc2626') {
          return new Promise((resolve) => {
            const modal = document.getElementById('confirm-modal');
            const titleEl = document.getElementById('confirm-modal-title');
            const msgEl = document.getElementById('confirm-modal-msg');
            const confirmBtn = document.getElementById('confirm-modal-btn-confirm');
            const cancelBtn = document.getElementById('confirm-modal-btn-cancel');

            titleEl.innerHTML = title;
            msgEl.innerText = message;
            confirmBtn.innerText = confirmBtnText;
            confirmBtn.style.background = confirmBtnColor;

            modal.style.display = 'flex';

            const cleanup = () => {
              modal.style.display = 'none';
              confirmBtn.removeEventListener('click', onConfirm);
              cancelBtn.removeEventListener('click', onCancel);
              modal.removeEventListener('click', onBackdrop);
            };
            const onConfirm = () => { cleanup(); resolve(true); };
            const onCancel = () => { cleanup(); resolve(false); };
            const onBackdrop = (e) => { if (e.target === modal) onCancel(); };

            confirmBtn.addEventListener('click', onConfirm);
            cancelBtn.addEventListener('click', onCancel);
            modal.addEventListener('click', onBackdrop);
          });
        }

        async function initAdminData() {
          await fetchHierarchy();
          if (currentAdmin && currentAdmin.role === 'rcsm') {
            const meta = getHierarchyMeta();
            meta.regions.forEach(r => selectedFeedRegions.add(r));
            meta.regions.forEach(r => {
              if (meta.centersByRegion[r]) {
                meta.centersByRegion[r].forEach(c => selectedFeedCenters.add(c));
              }
            });
            pruneFeedSelections(meta);
            updateFeedFilterDropdowns();
            await fetchData();
          } else {
            updateFeedFilterDropdowns();
            filterAndRenderFeed();
          }
          if (currentAdmin && currentAdmin.role === 'super_admin') {
            fetchUsers();
          }
        }

        // Initialize Page
        if (checkAdminAuth()) {
          initAdminData();
        }
        setInterval(() => {
          if (currentAdmin && (currentAdmin.role === 'super_admin' || currentAdmin.role === 'rcsm')) {
            const currentTab = document.querySelector('.tab-btn.active');
            if (currentTab && currentTab.id === 'tab-btn-feed') {
              if (selectedFeedRegions.size > 0 && selectedFeedCenters.size > 0) {
                fetchData();
              }
            }
          }
        }, 15000);
      </script>
    </body>
    </html>
    """


# Mount static files for the mobile PWA web app
app.mount("/", StaticFiles(directory=WEB_APP_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=9001, reload=True)

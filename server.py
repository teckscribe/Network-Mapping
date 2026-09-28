"""
GPON Field Survey Central Server
FastAPI + SQLite backend with Offline-First Sync, User Authentication, Center Filtering, and Central Office Dashboard.
"""

import os
import sqlite3
import datetime
import socket
import io
import json
import re
import uuid
import zipfile
import shutil
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Response
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

def save_users_to_json(conn=None):
    """Persists current SQLite users to users_config.json so git pulls never wipe user credentials."""
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_at_end = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, password, full_name, assigned_center, assigned_region, role, created_at, email FROM users")
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
        for u in users_list:
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
                u["username"].strip(),
                u["password"].strip(),
                u["full_name"].strip(),
                u.get("email", "").strip(),
                u["assigned_center"].strip(),
                u.get("assigned_region", "Thrissur"),
                normalize_role(u.get("role", "field_technician")),
                u.get("created_at", now)
            ))
        conn.commit()
        print(f"[Config] Restored and verified {len(users_list)} users from persistent {USERS_CONFIG_PATH}")
        return True
    except Exception as e:
        print(f"[Config Warning] Could not restore users from JSON backup: {e}")
        return False

# Init SQLite Database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
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

    # Check and add columns if upgrading existing db
    cur.execute("PRAGMA table_info(users)")
    user_cols = [c[1] for c in cur.fetchall()]
    if "email" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
    cur.execute("UPDATE users SET email = '' WHERE email IS NULL")

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
        """, ("admin", "admin123", "Central Super Administrator", "admin@gpon.local", "ALL", "ALL", "super_admin", now))

    # Normalize existing legacy roles in SQLite table
    cur.execute("UPDATE users SET role = 'super_admin' WHERE role = 'admin'")
    cur.execute("UPDATE users SET role = 'field_technician' WHERE role = 'field_agent'")

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

def load_hierarchy_data() -> dict:
    """Loads network hierarchy from HIERARCHY_FILE (or fallback to legacy custom_hierarchy.json)."""
    if os.path.exists(HIERARCHY_FILE):
        try:
            with open(HIERARCHY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Hierarchy Warning] Failed loading {HIERARCHY_FILE}: {e}")
    legacy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_hierarchy_data(data: dict):
    """Saves network hierarchy to HIERARCHY_FILE and mirrors to legacy path for backward compatibility."""
    os.makedirs(NODE_MASTER_DIR, exist_ok=True)
    with open(HIERARCHY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        legacy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
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

    # 3. Clean up temporary static .xlsx / .bak export files from BASE_DIR and Node Master
    for dir_to_clean in [BASE_DIR, NODE_MASTER_DIR]:
        if os.path.exists(dir_to_clean):
            for fname in os.listdir(dir_to_clean):
                if fname.endswith(".bak") or (fname.endswith(".xlsx") and any(k in fname.lower() for k in ["export", "template", "uploaded", "latest", "mapping", "olt"])):
                    try:
                        os.remove(os.path.join(dir_to_clean, fname))
                    except Exception:
                        pass

init_db()
ensure_directories_and_migrate()

app = FastAPI(title="GPON Field Survey Server")

# Allow CORS for mobile web requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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

# ====================
# AUTHENTICATION API
# ====================

@app.post("/api/login")
def login(req: LoginRequest):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND password = ?", (req.username.strip(), req.password.strip()))
    user = cur.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

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
def get_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT username, full_name, email, assigned_center, assigned_region, role, created_at FROM users")
    users = [
        {
            "username": u["username"],
            "full_name": u["full_name"],
            "email": u["email"] or "",
            "assigned_center": u["assigned_center"],
            "assigned_centers": [c.strip() for c in (u["assigned_center"] or "").split(",") if c.strip()],
            "assigned_region": u["assigned_region"],
            "assigned_regions": [r.strip() for r in (u["assigned_region"] or "Thrissur").split(",") if r.strip()],
            "role": normalize_role(u["role"]),
            "role_label": VALID_ROLES.get(normalize_role(u["role"]), "Field Technician"),
            "created_at": u["created_at"]
        } for u in cur.fetchall()
    ]
    conn.close()
    return {"users": users}

@app.post("/api/users")
def create_user(u: UserCreateModel):
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
        password_to_store = existing_row[0]
    else:
        password_to_store = (u.password or "").strip() or "1234"

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

@app.post("/api/change-password")
def change_password(req: ChangePasswordRequest):
    uname = req.username.strip()
    email_in = req.email.strip().lower()
    new_pwd = req.new_password.strip()

    if not uname or not email_in or not new_pwd:
        raise HTTPException(status_code=400, detail="Username, registered email address, and new password are required.")

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

    cur.execute("UPDATE users SET password = ? WHERE username = ?", (new_pwd, user["username"]))
    conn.commit()
    conn.close()

    save_users_to_json()
    return {"status": "success", "message": "Password updated successfully! You can now log in with your new password / PIN."}

@app.delete("/api/users/{username}")
def delete_user(username: str):
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
def export_users_config():
    save_users_to_json()
    if os.path.exists(USERS_CONFIG_PATH):
        return FileResponse(USERS_CONFIG_PATH, media_type="application/json", filename="users_config.json")
    raise HTTPException(status_code=404, detail="Configuration not found")

@app.post("/api/config/users")
async def import_users_config(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        users_list = json.loads(contents.decode('utf-8'))
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
def get_hierarchy():
    hier = load_hierarchy_data()
    if hier:
        return {"hierarchy": hier}
    default_hierarchy = {
        "THRISSUR NORTH": {
            "Mulamkunnathukavu": {
                "THN156 OLT53 Mulamkunnathukavu": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
            }
        },
        "THATHAMANGALAM": {
            "Kollengode": {
                "TMM/25/OLT-08-KOLLEMGODE": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
            }
        }
    }
    return {"hierarchy": default_hierarchy}

@app.post("/api/upload-hierarchy")
def upload_hierarchy(payload: dict):
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
async def upload_hierarchy_excel(file: UploadFile = File(...)):
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

        # Merge with existing custom hierarchy without duplicates
        existing_hierarchy = load_hierarchy_data()

        for c, rts in new_hierarchy.items():
            if c not in existing_hierarchy:
                existing_hierarchy[c] = {}
            for rt, olts in rts.items():
                if rt not in existing_hierarchy[c]:
                    existing_hierarchy[c][rt] = {}
                for olt_k, olt_data in olts.items():
                    target_k = olt_k
                    for exist_k in list(existing_hierarchy[c][rt].keys()):
                        if exist_k.strip().lower() == olt_k.lower():
                            target_k = exist_k
                            break
                    existing_hierarchy[c][rt][target_k] = olt_data

        save_hierarchy_data(existing_hierarchy)

        return {
            "status": "success",
            "message": f"Imported {total_olts} unique Nodes across {len(centers_found)} Centers successfully into Node Master!",
            "total_olts": total_olts,
            "centers": list(centers_found),
            "hierarchy": existing_hierarchy
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse Excel: {str(e)}")

@app.post("/api/hierarchy/olt")
def save_or_edit_olt(payload: OLTEditModel):
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
def delete_olt(payload: OLTDeleteModel):
    hierarchy = load_hierarchy_data()
    c = payload.center.strip()
    rt = payload.rt_room.strip()
    olt = payload.olt_name.strip()

    if c in hierarchy and rt in hierarchy[c]:
        deleted = False
        for k in list(hierarchy[c][rt].keys()):
            if k.strip().lower() == olt.lower():
                del hierarchy[c][rt][k]
                deleted = True
                break
        
        if not hierarchy[c][rt]:
            del hierarchy[c][rt]
        if not hierarchy[c]:
            del hierarchy[c]

        if deleted:
            save_hierarchy_data(hierarchy)
            return {"status": "success", "message": f"Deleted Node '{olt}' successfully."}
    
    raise HTTPException(status_code=404, detail="Node not found in hierarchy.")

@app.post("/api/hierarchy/bulk-delete")
def bulk_delete_olts(payload: dict):
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
        
        if c in hierarchy and rt in hierarchy[c]:
            for k in list(hierarchy[c][rt].keys()):
                if k.strip().lower() == olt.lower():
                    del hierarchy[c][rt][k]
                    deleted_count += 1
                    break
            
            if not hierarchy[c][rt]:
                del hierarchy[c][rt]
            if c in hierarchy and not hierarchy[c]:
                del hierarchy[c]
    
    save_hierarchy_data(hierarchy)
    return {"status": "success", "message": f"Deleted {deleted_count} Node(s) successfully.", "deleted": deleted_count}

@app.delete("/api/hierarchy/clear")
def clear_hierarchy():
    save_hierarchy_data({})
    return {"status": "success", "message": "All hierarchy data cleared."}

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
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM survey_records")
    total = cur.fetchone()[0]
    conn.close()
    return {"status": "ok", "total_server_records": total, "server_time": datetime.datetime.now().isoformat()}

@app.post("/api/sync")
def sync_records(payload: SyncPayload):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now_str = datetime.datetime.now().isoformat()
    synced_uuids = []

    for r in payload.records:
        try:
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
                payload.device_id, r.surveyor_username, r.surveyor_name,
                r.created_at or now_str, now_str
            ))
            synced_uuids.append(r.client_uuid)
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
        "total_server_records": total_count
    }

@app.get("/api/records")
def get_all_records(center: Optional[str] = None, region: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    query = "SELECT * FROM survey_records WHERE 1=1"
    params = []
    if center and center.strip() and center.strip().upper() != "ALL":
        query += " AND LOWER(TRIM(center)) = LOWER(TRIM(?))"
        params.append(center.strip())
    if region and region.strip() and region.strip().upper() != "ALL":
        query += " AND LOWER(TRIM(region)) = LOWER(TRIM(?))"
        params.append(region.strip())
    query += " ORDER BY rowid DESC"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"records": rows, "count": len(rows)}

@app.delete("/api/records/{client_uuid}")
def delete_record(client_uuid: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM survey_records WHERE client_uuid = ?", (client_uuid,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Record not found.")
    return {"status": "success", "message": "Record deleted successfully."}

@app.get("/api/export-excel")
def export_server_excel():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM survey_records ORDER BY rowid ASC")
    rows = cur.fetchall()
    conn.close()

    wb = build_excel_workbook(rows, title="Master_Data")
    excel_bytes = workbook_to_bytes(wb)
    today = datetime.date.today().isoformat()
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="GPON_Master_Network_Mapping_{today}.xlsx"'
        }
    )

@app.get("/api/export-center-excel")
def export_center_excel(center: str, region: Optional[str] = None):
    """Exports and downloads an individual Center's survey Excel file streamed dynamically in-memory."""
    if not center or not center.strip():
        raise HTTPException(status_code=400, detail="Center name is required.")
    
    cent_clean = center.strip()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) ORDER BY rowid ASC", (cent_clean,))
    rows = cur.fetchall()
    conn.close()

    safe_cent = sanitize_folder_name(cent_clean, "General")
    wb = build_excel_workbook(rows, title=safe_cent[:31])
    excel_bytes = workbook_to_bytes(wb)
    today = datetime.date.today().isoformat()
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_cent}_Survey_Data_{today}.xlsx"'
        }
    )

@app.get("/api/export-data-zip")
def export_data_zip():
    """Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory."""
    hierarchy = load_hierarchy_data()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM survey_records ORDER BY rowid ASC")
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

    # Also include any centers present in hierarchy
    for c, rts in hierarchy.items():
        c_clean = c.strip()
        reg = get_region_for_center(c_clean, hierarchy) or "Thrissur"
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
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="GPON_Survey_Data_All_Centers_{today}.zip"'
        }
    )

@app.post("/api/upload-survey-excel")
async def upload_survey_excel(file: UploadFile = File(...)):
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
                        client_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{enclosure_id}_{survey_dt}"))
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
def get_data_folders_summary():
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
        
    return {
        "status": "success",
        "data_storage": "SQLite Database (In-Memory Excel Streaming)",
        "total_regions": len(result_regions),
        "total_centers": total_centers,
        "total_records": total_records,
        "regions": result_regions
    }

@app.post("/api/resync-data-folders")
def resync_data_folders():
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
def admin_dashboard():
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
              <input type="password" id="admin-login-pass" required style="width:100%; padding:9px 12px;" placeholder="Password">
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
            Logged in as: <strong id="header-user-name">Administrator</strong> | Center: <span id="header-user-center">ALL</span>
          </p>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
          <input type="file" id="top-survey-upload-input" accept=".xlsx, .xls, .csv" style="display:none;" onchange="uploadSurveyExcel(event)">
          <button id="btn-top-import" class="btn" style="background:#0f766e; color:white;" onclick="document.getElementById('top-survey-upload-input').click()">📤 Import Survey Excel</button>
          <a href="/api/export-excel" class="btn btn-green">📊 Master Excel</a>
          <a href="/api/export-data-zip" class="btn" style="background:#2563eb; color:white; text-decoration:none;">🗂️ All Centers (ZIP)</a>
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
            <div>
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Filter Region:</label>
              <select id="feed-filter-region" onchange="onFeedRegionChanged()" style="min-width:140px;">
                <option value="ALL">All Regions</option>
              </select>
            </div>
            <div>
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Filter Center:</label>
              <select id="feed-filter-center" onchange="onFeedCenterChanged()" style="min-width:180px;">
                <option value="ALL">All Centers</option>
              </select>
            </div>
            <div style="flex:1; min-width:200px;">
              <label style="display:block; font-size:0.75rem; font-weight:700; color:#475569; margin-bottom:2px;">Search Survey Data:</label>
              <input type="text" id="feed-search" placeholder="🔍 Search Enclosure, Post #, Landmark, Surveyor..." oninput="filterAndRenderFeed()" style="width:100%;">
            </div>
          </div>

          <div style="display:flex; gap:8px; align-items:flex-end;">
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
              <tr><td colspan="15" style="text-align:center; padding:20px; color:#64748b;">Loading survey records...</td></tr>
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
              2. <strong>RCSM</strong>: Center dashboard & downloads only | 
              3. <strong>ACSO</strong>: Field entry & download | 
              4. <strong>Field Tech</strong>: Field entry only
            </p>
          </div>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <button type="button" onclick="openAddUserModal()" class="btn btn-green" style="font-size:0.85rem; padding:8px 16px; font-weight:700; box-shadow:0 1px 3px rgba(0,0,0,0.1);">➕ Add User</button>
            <a href="/api/config/users" download="users_config.json" class="btn" style="background:#0284c7; font-size:0.8rem; padding:7px 12px;">📥 Backup JSON</a>
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
                <option value="rcsm">📊 RCSM (Center Dashboard & Downloads)</option>
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
                  <span id="region-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">ALL Regions</span>
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
                  <span id="center-picker-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">ALL (All Network)</span>
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
              <div id="selected-centers-tags-bar" style="display:flex; flex-wrap:wrap; gap:6px;"></div>
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
            <a href="/api/export-data-zip" class="btn" style="background:#2563eb; color:white; font-size:0.85rem; text-decoration:none;">🗂️ Download All Centers (ZIP)</a>
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
          let userStr = localStorage.getItem('gpon_admin_user') || localStorage.getItem('gpon_logged_in_user');
          if (!userStr) {
            showAdminLoginModal();
            return false;
          }

          try {
            currentAdmin = JSON.parse(userStr);
          } catch(e) {
            showAdminLoginModal();
            return false;
          }

          const role = normalizeAdminRole(currentAdmin.role);
          currentAdmin.role = role;

          if (role === 'field_technician' || role === 'acso') {
            document.getElementById('access-denied-modal').style.display = 'flex';
            document.getElementById('access-denied-msg').innerHTML = 
              `Your account <strong>${currentAdmin.username}</strong> is registered as <strong>${role === 'acso' ? 'ACSO' : 'Field Technician'}</strong>.<br>The Admin Portal is reserved for Super Admins and RCSMs.`;
            return false;
          }

          document.getElementById('admin-auth-overlay').style.display = 'none';
          document.getElementById('access-denied-modal').style.display = 'none';

          // Apply Role UI
          applyAdminRoleUI(role);
          return true;
        }

        function showAdminLoginModal() {
          document.getElementById('admin-auth-overlay').style.display = 'flex';
          document.getElementById('admin-login-error').style.display = 'none';
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
          currentAdmin = null;
          showAdminLoginModal();
        }

        function applyAdminRoleUI(role) {
          const nameEl = document.getElementById('header-user-name');
          const centerEl = document.getElementById('header-user-center');
          const badgeEl = document.getElementById('header-user-badge');

          if (currentAdmin) {
            nameEl.innerText = currentAdmin.full_name || currentAdmin.username;
            centerEl.innerText = currentAdmin.assigned_center || 'ALL';
          }

          const btnUsers = document.getElementById('tab-btn-users');
          const btnHierarchy = document.getElementById('tab-btn-hierarchy');
          const btnTopImport = document.getElementById('btn-top-import');
          const btnTab1Import = document.getElementById('btn-tab1-import');
          const thRecordActions = document.getElementById('th-record-actions');
          const btnOptimize = document.getElementById('btn-optimize-storage');

          if (role === 'super_admin') {
            badgeEl.innerText = '👑 Super Admin';
            badgeEl.style.background = '#6366f1';
            btnUsers.style.display = 'inline-block';
            btnHierarchy.style.display = 'inline-block';
            btnTopImport.style.display = 'inline-flex';
            btnTab1Import.style.display = 'inline-flex';
            thRecordActions.style.display = '';
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

        // Tab 1: Survey Records & Center Filter
        async function fetchData() {
          const region = document.getElementById('feed-filter-region').value;
          const center = document.getElementById('feed-filter-center').value;

          let url = '/api/records';
          const params = [];
          if (region && region !== 'ALL') params.push(`region=${encodeURIComponent(region)}`);
          if (center && center !== 'ALL') params.push(`center=${encodeURIComponent(center)}`);
          if (params.length > 0) url += '?' + params.join('&');

          try {
            const res = await fetch(url);
            const data = await res.json();
            cachedRecords = data.records || [];
            updateFeedFilterDropdowns();
            filterAndRenderFeed();
          } catch(e) {
            console.error('Failed to fetch records:', e);
          }
        }

        function getHierarchyMeta() {
          const regionsSet = new Set();
          const centersByRegion = {};
          const centerToRegion = {};

          Object.keys(cachedHierarchy).forEach(center => {
            const rts = cachedHierarchy[center] || {};
            let centerRegion = null;
            Object.keys(rts).forEach(rt => {
              const olts = rts[rt] || {};
              Object.keys(olts).forEach(oltName => {
                const entry = olts[oltName];
                if (entry && typeof entry === 'object' && entry.region) {
                  centerRegion = entry.region.trim();
                }
              });
            });
            if (!centerRegion) centerRegion = 'Thrissur';

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
            allCenters: Object.keys(cachedHierarchy).sort()
          };
        }

        // Tab 1 Filters: Strictly ONLY regions and centers from uploaded Node Master Excel
        function updateFeedFilterDropdowns() {
          const meta = getHierarchyMeta();
          const regSelect = document.getElementById('feed-filter-region');
          const centerSelect = document.getElementById('feed-filter-center');
          if (!regSelect || !centerSelect) return;

          const curReg = regSelect.value || 'ALL';
          const curCenter = centerSelect.value || 'ALL';

          regSelect.innerHTML = '<option value="ALL">All Regions</option>';
          meta.regions.forEach(reg => {
            const opt = document.createElement('option');
            opt.value = reg;
            opt.innerText = reg;
            if (reg.toLowerCase() === curReg.toLowerCase()) opt.selected = true;
            regSelect.appendChild(opt);
          });
          if (curReg === 'ALL') regSelect.value = 'ALL';

          updateFeedCenterFilterOptions(curReg, curCenter);
        }

        function updateFeedCenterFilterOptions(selectedReg, curCenter) {
          const meta = getHierarchyMeta();
          const centerSelect = document.getElementById('feed-filter-center');
          if (!centerSelect) return;

          const prev = curCenter || centerSelect.value || 'ALL';
          centerSelect.innerHTML = '<option value="ALL">All Centers</option>';

          let centerList = [];
          if (!selectedReg || selectedReg === 'ALL') {
            centerList = meta.allCenters;
          } else if (meta.centersByRegion[selectedReg]) {
            centerList = Array.from(meta.centersByRegion[selectedReg]).sort();
          }

          centerList.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c;
            opt.innerText = c;
            if (prev && c.trim().toLowerCase() === prev.trim().toLowerCase()) opt.selected = true;
            centerSelect.appendChild(opt);
          });

          if (prev && (prev === 'ALL' || centerList.some(c => c.trim().toLowerCase() === prev.trim().toLowerCase()))) {
            const matched = centerList.find(c => c.trim().toLowerCase() === prev.trim().toLowerCase());
            centerSelect.value = matched || 'ALL';
          } else {
            centerSelect.value = 'ALL';
          }
        }

        function onFeedRegionChanged() {
          const reg = document.getElementById('feed-filter-region').value;
          updateFeedCenterFilterOptions(reg, 'ALL');
          fetchData();
        }

        function onFeedCenterChanged() {
          fetchData();
        }

        // State for User Multi-Region and Multi-Center selection
        let selectedUserRegions = new Set(['ALL']);
        let selectedUserCenters = new Set(['ALL']);

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
          if (selectedUserRegions.size === 0) selectedUserRegions = new Set(['ALL']);
          if (selectedUserCenters.size === 0) selectedUserCenters = new Set(['ALL']);

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
              <input type="checkbox" value="ALL" ${isAllChecked ? 'checked' : ''} onchange="toggleAllRegionsCheckbox(this.checked)">
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
          if (selectedUserRegions.size === 0) {
            selectedUserRegions.add('ALL');
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

          if (selectedUserCenters.size === 0) {
            selectedUserCenters.add('ALL');
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
          }

          if (selectedUserCenters.size === 0) selectedUserCenters.add('ALL');
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
          if (selectedUserCenters.size === 0) {
            selectedUserCenters.add('ALL');
          }
          const meta = getHierarchyMeta();
          renderCenterCheckboxes(meta);
          updateUserPickerLabelsAndBadges();
        }

        function filterAndRenderFeed() {
          const q = (document.getElementById('feed-search').value || '').toLowerCase().trim();
          const isSuperAdmin = (currentAdmin && currentAdmin.role === 'super_admin');

          let filtered = cachedRecords;
          if (q) {
            filtered = cachedRecords.filter(r => 
              (r.enclosure_id || '').toLowerCase().includes(q) ||
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
            tbody.innerHTML = '<tr><td colspan="15" style="text-align:center; padding:20px; color:#94a3b8;">No survey records match current filters.</td></tr>';
            return;
          }

          filtered.forEach((r, idx) => {
            const tr = document.createElement('tr');
            const timeDisplay = (r.survey_date_time || r.created_at || r.synced_at || '').slice(0, 19).replace('T', ' ');
            
            let actionHtml = '';
            if (isSuperAdmin) {
              actionHtml = `<td style="text-align:center; white-space:nowrap;">
                <button class="btn btn-danger" style="padding:3px 7px; font-size:0.75rem;" onclick="deleteSurveyRecord('${r.client_uuid}')" title="Delete Record">🗑️</button>
              </td>`;
            } else {
              actionHtml = `<td style="display:none;"></td>`;
            }

            tr.innerHTML = `
              <td style="color:#64748b; font-family:monospace; font-size:0.8rem;">${idx + 1}</td>
              <td style="font-family:monospace; font-size:0.8rem; color:#64748b;">${timeDisplay || '-'}</td>
              <td><strong style="color:#0284c7;">${r.enclosure_id || '-'}</strong></td>
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
        }

        function downloadSelectedCenterExcel() {
          const region = document.getElementById('feed-filter-region').value;
          const center = document.getElementById('feed-filter-center').value;

          if (center && center !== 'ALL') {
            window.location.href = `/api/export-center-excel?center=${encodeURIComponent(center)}&region=${encodeURIComponent(region)}`;
          } else {
            window.location.href = '/api/export-excel';
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
            const res = await fetch(`/api/records/${uuid}`, { method: 'DELETE' });
            if (res.ok) {
              fetchData();
            } else {
              const err = await res.json();
              alert('Error: ' + (err.detail || 'Could not delete record'));
            }
          } catch(err) {
            alert('Network error: ' + err.message);
          }
        }

        // Tab 2: User Access Management (4 Tiers)
        async function fetchUsers() {
          try {
            const res = await fetch('/api/users');
            const data = await res.json();
            const tbody = document.getElementById('users-table-body');
            tbody.innerHTML = '';

            const roleBadges = {
              'super_admin': '<span class="role-tag-super_admin">👑 Super Admin</span>',
              'rcsm': '<span class="role-tag-rcsm">📊 RCSM</span>',
              'acso': '<span class="role-tag-acso">📝 ACSO</span>',
              'field_technician': '<span class="role-tag-field_technician">👷 Field Tech</span>'
            };

            data.users.forEach(u => {
              const tr = document.createElement('tr');
              const roleClean = normalizeAdminRole(u.role);
              const badgeHtml = roleBadges[roleClean] || `<span class="tag">${u.role}</span>`;
              const emailDisplay = u.email ? `<span style="font-family:monospace; font-size:0.8rem; color:#0369a1;">${u.email}</span>` : '<span style="color:#94a3b8; font-style:italic; font-size:0.78rem;">No Email</span>';
              
              tr.innerHTML = `
                <td><strong>${u.username}</strong></td>
                <td>${u.full_name}</td>
                <td>${emailDisplay}</td>
                <td><span style="background:#f1f5f9; padding:2px 8px; border-radius:4px; font-weight:600; font-size:0.8rem;">${u.assigned_region || 'Thrissur'}</span></td>
                <td>
                  ${(() => {
                    const centers = (u.assigned_center || 'ALL').split(',').map(s => s.trim()).filter(Boolean);
                    if (centers.length === 0 || centers.includes('ALL')) {
                      return '<span class="tag" style="background:#059669;">ALL (Network)</span>';
                    }
                    if (centers.length === 1) {
                      return `<span class="tag" style="background:#0284c7;">${centers[0]}</span>`;
                    }
                    if (centers.length <= 2) {
                      return centers.map(c => `<span class="tag" style="background:#0284c7; margin-right:3px;">${c}</span>`).join('');
                    }
                    return `<span class="tag" style="background:#0284c7;" title="${centers.join(', ')}">🏢 ${centers.length} Centers Charge</span>`;
                  })()}
                </td>
                <td>${badgeHtml}</td>
                <td style="color:#64748b; font-size:0.8rem; font-family:monospace;">${(u.created_at || '').slice(0, 19).replace('T', ' ')}</td>
                <td style="text-align:center; white-space:nowrap;">
                  <button class="btn btn-outline" style="padding:3px 8px; font-size:0.75rem; margin-right:4px;" onclick="openEditUserModal('${u.username}', '${encodeURIComponent(u.full_name)}', '${encodeURIComponent(u.email || '')}', '${encodeURIComponent(u.assigned_center)}', '${encodeURIComponent(u.assigned_region || 'Thrissur')}', '${roleClean}')">✏️ Edit</button>
                  ${u.username !== 'admin' ? `<button class="btn btn-danger" style="padding:3px 8px; font-size:0.75rem;" onclick="deleteUser('${u.username}')">🗑️</button>` : '<span style="color:#94a3b8; font-size:0.75rem;">Root</span>'}
                </td>
              `;
              tbody.appendChild(tr);
            });
          } catch(e) {
            console.error(e);
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

          populateUserRegionAndCenterDropdowns('ALL', 'ALL');
          document.getElementById('user-modal').style.display = 'flex';
        }

        function openEditUserModal(username, encName, encEmail, encCenter, encRegion, role) {
          const c = decodeURIComponent(encCenter);
          const reg = decodeURIComponent(encRegion);
          const email = decodeURIComponent(encEmail || '');

          document.getElementById('user-modal-title').innerHTML = `✏️ Edit User: ${username}`;
          document.getElementById('modal-new-user').value = username;
          document.getElementById('modal-new-user').setAttribute('readonly', 'true');
          document.getElementById('modal-new-user').style.background = '#f1f5f9';
          document.getElementById('modal-new-pass').value = '';
          document.getElementById('modal-new-pass').placeholder = '(Leave empty to keep existing password)';
          document.getElementById('modal-new-pass').removeAttribute('required');
          const passReq = document.getElementById('modal-pass-required');
          if (passReq) passReq.style.display = 'none';
          document.getElementById('modal-new-name').value = decodeURIComponent(encName);
          document.getElementById('modal-new-email').value = email;

          populateUserRegionAndCenterDropdowns(reg, c);

          document.getElementById('modal-new-role').value = role;
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
          
          const regionsArr = Array.from(selectedUserRegions);
          const centersArr = Array.from(selectedUserCenters);

          if (centersArr.length === 0) {
            alert('Please select at least one assigned center or ALL.');
            return;
          }

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
            const res = await fetch('/api/users', {
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
          await fetch('/api/users/' + u, { method: 'DELETE' });
          fetchUsers();
        }

        async function importUsersConfig(e) {
          const file = e.target.files[0];
          if (!file) return;
          const formData = new FormData();
          formData.append('file', file);
          try {
            const res = await fetch('/api/config/users', { method: 'POST', body: formData });
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
            const res = await fetch('/api/hierarchy');
            const data = await res.json();
            const hier = (data && data.hierarchy) ? data.hierarchy : data;
            cachedHierarchy = hier;
            const tbody = document.getElementById('hierarchy-table-body');
            tbody.innerHTML = '';

            let totalCenters = Object.keys(hier).length;
            let totalRTRooms = 0;
            let totalOLTs = 0;
            fullHierarchyRows = [];

            Object.keys(hier).sort().forEach(center => {
              const rts = hier[center];
              if (!rts || typeof rts !== 'object') return;
              Object.keys(rts).sort().forEach(rtRoom => {
                totalRTRooms++;
                const olts = rts[rtRoom];
                if (!olts || typeof olts !== 'object') return;
                Object.keys(olts).sort().forEach(oltName => {
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
            const res = await fetch('/api/hierarchy/olt', {
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
            const res = await fetch('/api/hierarchy/olt', {
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
            const res = await fetch('/api/hierarchy/bulk-delete', {
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
            const res = await fetch('/api/upload-hierarchy-excel', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (res.ok) {
              statusDiv.style.background = '#d1fae5';
              statusDiv.style.color = '#065f46';
              statusDiv.innerText = `✅ ${data.message || 'Hierarchy imported successfully!'}`;
              fetchHierarchy();
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
            const res = await fetch('/api/upload-survey-excel', {
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
            const res = await fetch('/api/data-folders-summary');
            const data = await res.json();
            
            document.getElementById('folder-stat-regions').innerText = data.total_regions || 0;
            document.getElementById('folder-stat-centers').innerText = data.total_centers || 0;
            document.getElementById('folder-stat-records').innerText = data.total_records || 0;

            const container = document.getElementById('folders-container');
            container.innerHTML = '';

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
                    <td>
                      <a href="/api/export-center-excel?center=${encodeURIComponent(c.center)}&region=${encodeURIComponent(c.region)}" 
                         class="btn btn-outline" 
                         style="padding:4px 10px; font-size:0.75rem; text-decoration:none;">
                        📥 Download Excel
                      </a>
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

        async function resyncFoldersAction() {
          try {
            const res = await fetch('/api/resync-data-folders', { method: 'POST' });
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
          await fetchData();
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
              fetchData();
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

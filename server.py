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
import zipfile
import shutil
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_APP_DIR = os.path.join(BASE_DIR, "web_app")
DB_PATH = os.path.join(BASE_DIR, "gpon_survey_data.db")
USERS_CONFIG_PATH = os.path.join(BASE_DIR, "users_config.json")
NODE_MASTER_DIR = os.path.join(BASE_DIR, "Node Master")
DATA_DIR = os.path.join(BASE_DIR, "data")
HIERARCHY_FILE = os.path.join(NODE_MASTER_DIR, "custom_hierarchy.json")

def save_users_to_json(conn=None):
    """Persists current SQLite users to users_config.json so git pulls never wipe user credentials."""
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_at_end = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, password, full_name, assigned_center, assigned_region, role, created_at FROM users")
        rows = cur.fetchall()
        users_list = []
        for r in rows:
            users_list.append({
                "username": r[0],
                "password": r[1],
                "full_name": r[2],
                "assigned_center": r[3],
                "assigned_region": r[4] or "Thrissur",
                "role": r[5] or "field_agent",
                "created_at": r[6]
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
            INSERT INTO users (username, password, full_name, assigned_center, assigned_region, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password=excluded.password,
                full_name=excluded.full_name,
                assigned_center=excluded.assigned_center,
                assigned_region=excluded.assigned_region,
                role=excluded.role
            """, (
                u["username"].strip(),
                u["password"].strip(),
                u["full_name"].strip(),
                u["assigned_center"].strip(),
                u.get("assigned_region", "Thrissur"),
                u.get("role", "field_agent"),
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
        assigned_center TEXT NOT NULL,
        assigned_region TEXT DEFAULT 'Thrissur',
        role TEXT DEFAULT 'field_agent',
        created_at TEXT
    )
    """)

    # Check and add columns if upgrading existing db
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

    # 2. Seed Default Users if none exist
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        now = datetime.datetime.now().isoformat()
        default_users = [
            ("thrissur_agent", "1234", "Thrissur Survey Agent", "Thrissur North", "Thrissur", "field_agent", now),
            ("tmm_agent", "1234", "Thathamangalam Agent", "Thathamangalm", "Palakkad", "field_agent", now),
            ("admin", "admin123", "Central Administrator", "ALL", "ALL", "admin", now)
        ]
        cur.executemany("""
        INSERT INTO users (username, password, full_name, assigned_center, assigned_region, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, default_users)
        conn.commit()
        print("Default users initialized.")

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

def get_center_excel_path(region: str, center: str) -> str:
    """Computes and ensures the directory for a Center's Excel spreadsheet path."""
    det_reg = region or get_region_for_center(center) or "Thrissur"
    safe_reg = sanitize_folder_name(det_reg, "Thrissur")
    safe_cent = sanitize_folder_name(center, "General")
    center_dir = os.path.join(DATA_DIR, safe_reg, safe_cent)
    os.makedirs(center_dir, exist_ok=True)
    return os.path.join(center_dir, f"{safe_cent}_Survey_Data.xlsx")

def update_center_excel(region: str, center: str) -> Optional[str]:
    """Generates / updates the Excel spreadsheet for a specific Center inside data/<Region>/<Center>/"""
    if not center or not str(center).strip():
        return None
    safe_center = str(center).strip()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM survey_records 
        WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))
        ORDER BY rowid ASC
    """, (safe_center,))
    rows = cur.fetchall()
    
    determined_region = region
    if rows and rows[0]['region']:
        determined_region = rows[0]['region']
    elif not determined_region or determined_region.lower() in ["", "none", "null"]:
        determined_region = get_region_for_center(safe_center) or "Thrissur"
        
    conn.close()
    
    file_path = get_center_excel_path(determined_region, safe_center)
    wb = build_excel_workbook(rows, title=safe_center[:31])
    wb.save(file_path)
    return file_path

def sync_all_center_excels(only_folders=False):
    """
    Ensures that for every (Region, Center) in custom_hierarchy.json AND in survey_records:
    1. Folder data/<safe_region>/<safe_center>/ exists.
    2. Excel spreadsheet data/<safe_region>/<safe_center>/<safe_center>_Survey_Data.xlsx is generated/updated.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    
    hierarchy = load_hierarchy_data()
    center_to_region = {}
    for center, rts in hierarchy.items():
        if not isinstance(rts, dict):
            continue
        center_to_region[center.strip()] = get_region_for_center(center, hierarchy)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT region, center FROM survey_records")
    db_pairs = cur.fetchall()
    conn.close()
    
    for r_reg, r_cent in db_pairs:
        if r_cent and str(r_cent).strip():
            c_clean = str(r_cent).strip()
            reg_clean = str(r_reg).strip() if (r_reg and str(r_reg).strip()) else center_to_region.get(c_clean, "Thrissur")
            center_to_region[c_clean] = reg_clean

    updated_files = []
    for center, region in center_to_region.items():
        try:
            safe_reg = sanitize_folder_name(region, "Thrissur")
            safe_cent = sanitize_folder_name(center, "General")
            center_dir = os.path.join(DATA_DIR, safe_reg, safe_cent)
            os.makedirs(center_dir, exist_ok=True)
            
            excel_path = os.path.join(center_dir, f"{safe_cent}_Survey_Data.xlsx")
            if not only_folders or not os.path.exists(excel_path):
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM survey_records 
                    WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))
                    ORDER BY rowid ASC
                """, (center,))
                rows = cur.fetchall()
                conn.close()
                
                wb = build_excel_workbook(rows, title=safe_cent[:31])
                wb.save(excel_path)
                updated_files.append(excel_path)
        except Exception as e:
            print(f"[Sync Warning] Could not sync Excel for center '{center}': {e}")
            
    return updated_files

def ensure_directories_and_migrate():
    """Initializes 'Node Master' and 'data' directories and migrates existing files."""
    os.makedirs(NODE_MASTER_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

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

    # 2. Archive any existing Node Master spreadsheets from BASE_DIR to Node Master/ if not present
    for fname in os.listdir(BASE_DIR):
        if (fname.endswith(".xlsx") or fname.endswith(".xls")) and any(k in fname.lower() for k in ["hierarchy", "olt", "node", "mapping"]):
            target_path = os.path.join(NODE_MASTER_DIR, fname)
            src_path = os.path.join(BASE_DIR, fname)
            if not os.path.exists(target_path) and os.path.isfile(src_path) and "export" not in fname.lower() and "survey_data" not in fname.lower():
                try:
                    shutil.copy2(src_path, target_path)
                    print(f"[Init] Preserved master spreadsheet {fname} -> {target_path}")
                except Exception as e:
                    print(f"[Init Warning] Could not copy {fname}: {e}")

    # 3. Synchronize / pre-create all Region and Center folders inside data/
    try:
        sync_all_center_excels(only_folders=False)
    except Exception as e:
        print(f"[Init Warning] Could not sync center excels: {e}")

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
    password: str
    full_name: str
    assigned_center: str
    assigned_region: Optional[str] = "Thrissur"
    role: Optional[str] = "field_agent"

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

    return {
        "status": "success",
        "user": {
            "username": user["username"],
            "full_name": user["full_name"],
            "assigned_center": user["assigned_center"],
            "assigned_region": user["assigned_region"],
            "role": user["role"]
        }
    }

@app.get("/api/users")
def get_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT username, full_name, assigned_center, assigned_region, role, created_at FROM users")
    users = [dict(u) for u in cur.fetchall()]
    conn.close()
    return {"users": users}

@app.post("/api/users")
def create_user(u: UserCreateModel):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cur.execute("""
        INSERT INTO users (username, password, full_name, assigned_center, assigned_region, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            password=excluded.password,
            full_name=excluded.full_name,
            assigned_center=excluded.assigned_center,
            assigned_region=excluded.assigned_region,
            role=excluded.role
        """, (u.username.strip(), u.password.strip(), u.full_name.strip(), u.assigned_center.strip(), u.assigned_region, u.role, now))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    save_users_to_json()
    return {"status": "success", "message": f"User {u.username} saved successfully."}

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
        "Thrissur North": {
            "Mulamkunnathukavu": {
                "THN156 OLT53 Mulamkunnathukavu": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
            }
        },
        "Thathamangalm": {
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
        sync_all_center_excels(only_folders=False)
        return {"status": "success", "message": "Server network hierarchy merged successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-hierarchy-excel")
async def upload_hierarchy_excel(file: UploadFile = File(...)):
    if not (file.filename.lower().endswith(".xlsx") or file.filename.lower().endswith(".xls") or file.filename.lower().endswith(".csv")):
        raise HTTPException(status_code=400, detail="Only Excel (.xlsx/.xls) or CSV files are supported.")
    
    contents = await file.read()
    
    # Save a physical archive copy into "Node Master" folder
    os.makedirs(NODE_MASTER_DIR, exist_ok=True)
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_upload_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', file.filename)
    saved_upload_path = os.path.join(NODE_MASTER_DIR, f"Node_Master_{timestamp_str}_{clean_upload_name}")
    try:
        with open(saved_upload_path, "wb") as f_out:
            f_out.write(contents)
        ext = os.path.splitext(file.filename)[1] or ".xlsx"
        latest_path = os.path.join(NODE_MASTER_DIR, f"Node_Master_Latest{ext}")
        with open(latest_path, "wb") as f_out:
            f_out.write(contents)
        print(f"[Node Master] Saved uploaded file to {saved_upload_path} and {latest_path}")
    except Exception as e:
        print(f"[Node Master Warning] Failed saving physical file: {e}")
        
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
        sync_all_center_excels(only_folders=False)

        return {
            "status": "success",
            "message": f"Imported {total_olts} unique Nodes across {len(centers_found)} Centers successfully! Saved to 'Node Master' folder.",
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
    update_center_excel(reg, c)

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
        ["Thrissur", "Thrissur North", "Mulamkunnathukavu", "GPON", "THN156 OLT53 Mulamkunnathukavu", "8P"],
        ["Thrissur", "Thrissur North", "Mulamkunnathukavu", "GPON", "THN-156-OLT-53- Mulamkunnathukavu", "8P"],
        ["Palakkad", "Thathamangalm", "Kollengode", "GPON", "TMM/25/OLT-08-KOLLEMGODE", "8P"],
        ["Palakkad", "Palakkad South", "Alathur", "GPON", "PLK/12/OLT-03-ALATHUR", "16P"]
    ]

    for r_idx, row in enumerate(sample_rows, 2):
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name="Calibri", size=10)

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

    template_path = os.path.join(BASE_DIR, "Network_Hierarchy_Template.xlsx")
    wb.save(template_path)
    return FileResponse(
        template_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="Network_Hierarchy_Template.xlsx"
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

    # Update per-center Excel spreadsheets in data/<Region>/<Center>/ in real time
    affected_centers = set()
    for r in payload.records:
        if r.center and str(r.center).strip():
            cent = str(r.center).strip()
            reg = r.region or get_region_for_center(cent) or "Thrissur"
            affected_centers.add((reg, cent))

    for reg, cent in affected_centers:
        try:
            update_center_excel(reg, cent)
        except Exception as err:
            print(f"[Sync Warning] Failed updating Excel for center '{cent}': {err}")

    return {
        "status": "success",
        "synced_count": len(synced_uuids),
        "synced_uuids": synced_uuids,
        "total_server_records": total_count
    }

@app.get("/api/records")
def get_all_records():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM survey_records ORDER BY rowid DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"records": rows, "count": len(rows)}

@app.get("/api/export-excel")
def export_server_excel():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM survey_records ORDER BY rowid ASC")
    rows = cur.fetchall()
    conn.close()

    wb = build_excel_workbook(rows, title="Master_Data")
    export_path = os.path.join(BASE_DIR, "GPON_Master_Server_Export.xlsx")
    wb.save(export_path)

    today = datetime.date.today().isoformat()
    return FileResponse(
        export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"GPON_Master_Network_Mapping_{today}.xlsx"
    )

@app.get("/api/export-center-excel")
def export_center_excel(center: str, region: Optional[str] = None):
    """Exports and downloads an individual Center's survey Excel file from data/<Region>/<Center>/"""
    if not center or not center.strip():
        raise HTTPException(status_code=400, detail="Center name is required.")
    
    reg = region or get_region_for_center(center.strip()) or "Thrissur"
    file_path = update_center_excel(reg, center.strip())
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"No survey data file found for center '{center}'.")
    
    safe_cent = sanitize_folder_name(center.strip(), "General")
    today = datetime.date.today().isoformat()
    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{safe_cent}_Survey_Data_{today}.xlsx"
    )

@app.get("/api/export-data-zip")
def export_data_zip():
    """Generates and downloads a ZIP archive of all region/center Excel files inside data/"""
    sync_all_center_excels()
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(DATA_DIR):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, BASE_DIR)
                zip_file.write(abs_path, rel_path)
                
    zip_buffer.seek(0)
    today = datetime.date.today().isoformat()
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="GPON_Survey_Data_All_Centers_{today}.zip"'
        }
    )

@app.get("/api/data-folders-summary")
def get_data_folders_summary():
    """Returns structured summary of region folders, center folders, record counts, and Excel files."""
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
            excel_abs_path = os.path.join(DATA_DIR, safe_reg, safe_cent, excel_name)
            
            center_list.append({
                "center": cent_name,
                "region": reg,
                "folder": center_rel_path,
                "excel_file": excel_name,
                "excel_exists": os.path.exists(excel_abs_path),
                "records_count": info["records_count"]
            })
            total_centers += 1
        result_regions[reg] = center_list
        
    return {
        "status": "success",
        "data_dir": "data",
        "node_master_dir": "Node Master",
        "total_regions": len(result_regions),
        "total_centers": total_centers,
        "total_records": total_records,
        "regions": result_regions
    }

@app.post("/api/resync-data-folders")
def resync_data_folders():
    """Manual re-synchronization of all center spreadsheets from SQLite."""
    updated = sync_all_center_excels(only_folders=False)
    return {"status": "success", "message": f"Synchronized {len(updated)} center Excel spreadsheets.", "updated_files": len(updated)}

# Central Office Web Dashboard
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Network Mapping</title>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f1f5f9; color: #0f172a; margin: 0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #cbd5e1; padding-bottom: 15px; margin-bottom: 20px; }
        .btn { background: #0284c7; color: white; border: none; border-radius: 8px; padding: 9px 14px; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; }
        .btn:hover { background: #0369a1; }
        .btn-green { background: #059669; }
        .btn-green:hover { background: #047857; }
        .btn-danger { background: #dc2626; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-card { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .stat-num { font-size: 2rem; font-weight: bold; color: #0284c7; }
        .stat-label { font-size: 0.85rem; color: #64748b; font-weight: 600; margin-top: 4px; }
        .tabs { display: flex; gap: 10px; margin-bottom: 16px; }
        .tab-btn { background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 700; }
        .tab-btn.active { background: #0284c7; color: white; border-color: #0284c7; }
        table { width: 100%; border-collapse: collapse; background: #ffffff; border-radius: 10px; overflow: hidden; font-size: 0.85rem; border: 1px solid #cbd5e1; }
        th, td { padding: 11px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; color: #0f172a; }
        th { background: #f8fafc; color: #334155; font-weight: 700; border-bottom: 2px solid #cbd5e1; }
        tr:hover { background: #f1f5f9; }
        .tag { font-size: 0.75rem; padding: 3px 8px; border-radius: 12px; background: #0284c7; color: white; font-weight: bold; }
        .form-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; }
        input, select { background: #ffffff; color: #0f172a; border: 1.5px solid #cbd5e1; padding: 8px 12px; border-radius: 6px; outline: none; }
        input:focus, select:focus { border-color: #0284c7; }
      </style>
    </head>
    <body>
      <div class="header">
        <div>
          <h1 style="margin:0; font-size:1.5rem; color:#0f172a;">📡 Network Mapping</h1>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <a href="/api/export-excel" class="btn btn-green">📊 Download Master Excel</a>
          <a href="/api/export-data-zip" class="btn" style="background:#2563eb; color:white; text-decoration:none;">🗂️ Download All Centers (ZIP)</a>
          <button onclick="fetchData()" class="btn">🔄 Refresh</button>
        </div>
      </div>

      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('feed')">📋 Survey Feed</button>
        <button class="tab-btn" onclick="switchTab('users')">👥 Field Users & Center Assignment</button>
        <button class="tab-btn" onclick="switchTab('hierarchy')">📡 Upload Node Master Data</button>
        <button class="tab-btn" onclick="switchTab('folders')">📁 Region & Center Folders</button>
      </div>

      <!-- Tab 1: Survey Feed -->
      <div id="tab-feed" style="background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <h3 style="margin-top:0; color:#0284c7;">Survey Submissions (Live Feed)</h3>
        <div style="overflow-x:auto;">
          <table>
            <thead>
              <tr>
                <th>Date & Time</th>
                <th>Enclosure ID</th>
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
              </tr>
            </thead>
            <tbody id="table-body">
              <tr><td colspan="12" style="text-align:center; padding:20px;">Loading data...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 2: User Management -->
      <div id="tab-users" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:12px;">
          <h3 style="margin:0; color:#0284c7;">Add / Manage Field Surveyors</h3>
          <div style="display:flex; gap:8px;">
            <a href="/api/config/users" download="users_config.json" class="btn" style="background:#0284c7; color:white; font-size:0.8rem; text-decoration:none; padding:6px 12px; border-radius:6px; display:inline-flex; align-items:center; gap:4px;">📥 Backup Users JSON</a>
            <button type="button" onclick="document.getElementById('import-users-file').click()" class="btn" style="background:#475569; color:white; font-size:0.8rem; padding:6px 12px; border-radius:6px; cursor:pointer;">📤 Restore Users JSON</button>
            <input type="file" id="import-users-file" accept=".json" style="display:none;" onchange="importUsersConfig(event)">
          </div>
        </div>
        
        <form onsubmit="createUser(event)" class="form-row">
          <input type="text" id="new-user" placeholder="Username (e.g. anoop)" required>
          <input type="text" id="new-pass" placeholder="Password / PIN" required>
          <input type="text" id="new-name" placeholder="Full Name" required>
          <select id="new-center" required>
            <option value="Thrissur North">Thrissur North</option>
            <option value="Thathamangalm">Thathamangalm</option>
            <option value="ALL">ALL (Admin / Supervisor)</option>
          </select>
          <button type="submit" class="btn btn-green">➕ Add / Update User</button>
        </form>

        <div style="overflow-x:auto;">
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Full Name</th>
                <th>Assigned Center</th>
                <th>Role</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="users-table-body">
              <tr><td colspan="5" style="text-align:center; padding:20px;">Loading users...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 3: Network Hierarchy Management -->
      <div id="tab-hierarchy" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
          <div>
            <h3 style="margin:0 0 6px 0; color:#0284c7;">Upload Node Master Data (Region, Center, RT Room, Node Name)</h3>
            <p style="margin:0; font-size:0.85rem; color:#64748b;">
              Upload your Excel file (<code>.xlsx</code>, <code>.xls</code>, <code>.csv</code>) containing <strong>Center</strong>, <strong>RT Room</strong>, <strong>Node Name</strong> (or <strong>Device IP</strong>), and optional <strong>Number of Ports</strong> (8 Port/16 Port/32 Port).
              All field surveyor devices will automatically download and cache this hierarchy upon connecting.
            </p>
          </div>
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <a href="/api/download-hierarchy-template" class="btn" style="background:#0f766e;">📥 Download Template (.xlsx)</a>
            <input type="file" id="hierarchy-upload-input" accept=".xlsx, .xls, .csv" style="display:none;" onchange="uploadHierarchyExcel(event)">
            <button class="btn btn-green" onclick="document.getElementById('hierarchy-upload-input').click()">📂 Browse & Upload Excel File</button>
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
          <input type="text" id="hierarchy-search" placeholder="🔍 Search Center, RT Room, or Node..." oninput="filterHierarchyTable()" style="flex:1; min-width:240px; max-width:400px;">
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <span id="selection-count" style="font-size:0.8rem; color:#64748b; font-weight:600; display:none;">0 selected</span>
            <button id="btn-delete-selected" onclick="deleteSelectedOlts()" class="btn btn-danger" style="padding:8px 12px; display:none;">🗑️ Delete Selected</button>
            <button onclick="fetchHierarchy()" class="btn" style="background:#64748b; padding:8px 12px;">🔄 Refresh Table</button>
          </div>
        </div>

        <div style="overflow-x:auto; max-height:480px; overflow-y:auto; border:1px solid #cbd5e1; border-radius:8px;">
          <table>
            <thead>
              <tr style="position:sticky; top:0; z-index:2;">
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
              <tr><td colspan="9" style="text-align:center; padding:20px;">Loading network hierarchy...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 4: Region & Center Data Folders -->
      <div id="tab-folders" style="display:none; background:#ffffff; border-radius:10px; padding:16px; border:1px solid #cbd5e1; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
          <div>
            <h3 style="margin:0 0 6px 0; color:#0284c7;">📁 Field Survey Data Folders (data/ &lt;Region&gt;/ &lt;Center&gt;/)</h3>
            <p style="margin:0; font-size:0.85rem; color:#64748b;">
              Field survey data is stored by Region and Center in the server's <code>data/</code> folder.
              Each center subfolder contains its live Excel spreadsheet (<code>&lt;Center&gt;_Survey_Data.xlsx</code>) updated in real time.
            </p>
          </div>
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <a href="/api/export-data-zip" class="btn" style="background:#2563eb; color:white; font-size:0.85rem; text-decoration:none;">🗂️ Download All Centers (ZIP)</a>
            <button onclick="resyncFoldersAction()" class="btn" style="background:#0f766e; color:white; font-size:0.85rem;">🔄 Re-sync All Excels</button>
          </div>
        </div>

        <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom:16px;">
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-regions" style="font-size:1.5rem; color:#2563eb;">0</div>
            <div class="stat-label">Total Regions</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-centers" style="font-size:1.5rem; color:#0284c7;">0</div>
            <div class="stat-label">Total Center Folders</div>
          </div>
          <div class="stat-card" style="padding:12px 16px;">
            <div class="stat-num" id="folder-stat-records" style="font-size:1.5rem; color:#059669;">0</div>
            <div class="stat-label">Total Survey Records</div>
          </div>
        </div>

        <div id="folders-container">
          <p style="text-align:center; padding:20px; color:#64748b;">Loading folder hierarchy...</p>
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
                <input type="text" id="modal-region" required style="width:100%; box-sizing:border-box;" value="Thrissur" placeholder="e.g. Thrissur">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Center</label>
                <input type="text" id="modal-center" required style="width:100%; box-sizing:border-box;" placeholder="e.g. CHALAKKUDY">
              </div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">RT Room</label>
                <input type="text" id="modal-rtroom" required style="width:100%; box-sizing:border-box;" placeholder="e.g. Potta">
              </div>
              <div>
                <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">GPON / FTTH / WDM / EDFA</label>
                <select id="modal-tech" style="width:100%; box-sizing:border-box;">
                  <option value="GPON">GPON</option>
                  <option value="FTTH">FTTH</option>
                  <option value="WDM">WDM</option>
                  <option value="EDFA">EDFA</option>
                </select>
              </div>
            </div>
            <div style="margin-bottom:12px;">
              <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Node Name</label>
              <input type="text" id="modal-olt" required style="width:100%; box-sizing:border-box;" placeholder="e.g. CKY/116/OLT 01/Potta-1">
            </div>
            <div style="margin-bottom:18px;">
              <label style="display:block; font-size:0.8rem; font-weight:600; margin-bottom:4px;">Number of Ports</label>
              <select id="modal-ports" style="width:100%; box-sizing:border-box;">
                <option value="8 P">8 Port</option>
                <option value="16 P">16 Port</option>
                <option value="32 P">32 Port</option>
              </select>
            </div>
            <div style="display:flex; justify-content:flex-end; gap:8px;">
              <button type="button" class="btn" style="background:#94a3b8; color:white;" onclick="closeOltModal()">Cancel</button>
              <button type="submit" class="btn btn-green">💾 Save Node</button>
            </div>
          </form>
        </div>
      </div>

      <!-- UI Confirmation Modal (Confirm / Cancel) -->
      <div id="confirm-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(15,23,42,0.65); backdrop-filter:blur(3px); z-index:10001; justify-content:center; align-items:center; padding:16px;">
        <div style="background:white; border-radius:12px; padding:24px; max-width:440px; width:100%; box-shadow:0 20px 25px -5px rgba(0,0,0,0.25), 0 8px 10px -6px rgba(0,0,0,0.1); border:1px solid #cbd5e1;">
          <h3 id="confirm-modal-title" style="margin-top:0; color:#0f172a; font-size:1.15rem; display:flex; align-items:center; gap:8px;">
            ⚠️ Confirm Action
          </h3>
          <p id="confirm-modal-msg" style="color:#475569; font-size:0.92rem; line-height:1.5; margin:12px 0 24px 0;">
            Are you sure you want to proceed?
          </p>
          <div style="display:flex; justify-content:flex-end; gap:10px;">
            <button type="button" id="confirm-modal-btn-cancel" class="btn" style="background:#f1f5f9; color:#475569; border:1px solid #cbd5e1; font-weight:600; padding:9px 18px; border-radius:6px; font-size:0.9rem; cursor:pointer;">Cancel</button>
            <button type="button" id="confirm-modal-btn-confirm" class="btn btn-danger" style="font-weight:600; padding:9px 20px; border-radius:6px; font-size:0.9rem; cursor:pointer;">Confirm</button>
          </div>
        </div>
      </div>

      <script>
        let fullHierarchyRows = [];

        function switchTab(t) {
          document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
          document.getElementById('tab-feed').style.display = 'none';
          document.getElementById('tab-users').style.display = 'none';
          document.getElementById('tab-hierarchy').style.display = 'none';
          document.getElementById('tab-folders').style.display = 'none';

          if (t === 'feed') {
            document.querySelectorAll('.tab-btn')[0].classList.add('active');
            document.getElementById('tab-feed').style.display = 'block';
          } else if (t === 'users') {
            document.querySelectorAll('.tab-btn')[1].classList.add('active');
            document.getElementById('tab-users').style.display = 'block';
            fetchUsers();
          } else if (t === 'hierarchy') {
            document.querySelectorAll('.tab-btn')[2].classList.add('active');
            document.getElementById('tab-hierarchy').style.display = 'block';
            fetchHierarchy();
          } else if (t === 'folders') {
            document.querySelectorAll('.tab-btn')[3].classList.add('active');
            document.getElementById('tab-folders').style.display = 'block';
            fetchFoldersSummary();
          }
        }

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
                         class="btn" 
                         style="padding:5px 10px; font-size:0.75rem; background:#0284c7; text-decoration:none;">
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
                        <th>Server Folder Path</th>
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
            alert(data.message || 'All center Excels synchronized successfully!');
            fetchFoldersSummary();
          } catch(err) {
            alert('Failed to resync: ' + err.message);
          }
        }

        async function fetchData() {
          try {
            const res = await fetch('/api/records');
            const data = await res.json();
            const cntEl = document.getElementById('total-count');
            if (cntEl) cntEl.innerText = data.count;
            
            let custTotal = 0;
            const tbody = document.getElementById('table-body');
            tbody.innerHTML = '';

            if (data.records.length === 0) {
              tbody.innerHTML = '<tr><td colspan="12" style="text-align:center; padding:20px; color:#94a3b8;">No records synced from field devices yet.</td></tr>';
              return;
            }

            data.records.forEach(r => {
              custTotal += (r.customers_connected || 0);

              const tr = document.createElement('tr');
              const timeDisplay = (r.survey_date_time || r.created_at || r.synced_at || '').slice(0, 19).replace('T', ' ');
              tr.innerHTML = `
                <td style="font-family:monospace; font-size:0.8rem; color:#64748b;">${timeDisplay || '-'}</td>
                <td><strong style="color:#0284c7;">${r.enclosure_id || '-'}</strong></td>
                <td>${r.center || '-'} / ${r.rt_room || '-'}</td>
                <td>${r.olt_name || '-'} [${r.port_number || '-'}]</td>
                <td><strong>${r.kseb_post_number || '-'}</strong></td>
                <td>${r.landmark || '-'}</td>
                <td><span style="font-family:monospace; font-size:0.8rem;">${r.lat_long || '-'}</span></td>
                <td><span class="tag">${r.customers_connected || 0}</span></td>
                <td><span style="background:#e0f2fe; color:#0369a1; padding:2px 6px; border-radius:4px; font-weight:600; font-size:0.75rem;">${r.splitter_lead_color || '-'}</span></td>
                <td style="font-family:monospace; font-size:0.8rem;">${r.adl_subscriber_id || '-'}</td>
                <td style="font-family:monospace; font-size:0.8rem;">${r.acs_subscriber_id || '-'}</td>
                <td><strong style="color:#0284c7;">${r.surveyor_name || r.surveyor_username || 'App'}</strong></td>
              `;
              tbody.appendChild(tr);
            });

            const custEl = document.getElementById('total-customers');
            if (custEl) custEl.innerText = custTotal;
          } catch(e) {
            console.error(e);
          }
          fetchUsers();
          fetchHierarchyQuick();
        }

        async function fetchHierarchyQuick() {
          try {
            const res = await fetch('/api/hierarchy');
            const data = await res.json();
            const centerSelect = document.getElementById('new-center');
            const existingVals = Array.from(centerSelect.options).map(o => o.value);
            const hier = (data && data.hierarchy) ? data.hierarchy : data;
            Object.keys(hier).forEach(c => {
              if (!existingVals.includes(c)) {
                const opt = document.createElement('option');
                opt.value = c;
                opt.innerText = c;
                centerSelect.insertBefore(opt, centerSelect.lastElementChild);
              }
            });
          } catch(e) {}
        }

        async function fetchHierarchy() {
          try {
            const res = await fetch('/api/hierarchy');
            const data = await res.json();
            const hier = (data && data.hierarchy) ? data.hierarchy : data;
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
            fetchHierarchyQuick();
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
                <button class="btn" style="padding:4px 8px; font-size:0.75rem; background:#0284c7; color:white; margin-right:4px;" onclick="openEditOltModal('${encodeURIComponent(r.region)}', '${encodeURIComponent(r.center)}', '${encodeURIComponent(r.rtRoom)}', '${encodeURIComponent(r.tech)}', '${encodeURIComponent(r.oltName)}', '${encodeURIComponent(r.oltType)}')">✏️ Edit</button>
                <button class="btn btn-danger" style="padding:4px 8px; font-size:0.75rem;" onclick="deleteOlt('${encodeURIComponent(r.center)}', '${encodeURIComponent(r.rtRoom)}', '${encodeURIComponent(r.oltName)}')">🗑️</button>
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
            const onConfirm = () => {
              cleanup();
              resolve(true);
            };
            const onCancel = () => {
              cleanup();
              resolve(false);
            };
            const onBackdrop = (e) => {
              if (e.target === modal) onCancel();
            };

            confirmBtn.addEventListener('click', onConfirm);
            cancelBtn.addEventListener('click', onCancel);
            modal.addEventListener('click', onBackdrop);
          });
        }

        async function deleteSelectedOlts() {
          const checked = document.querySelectorAll('#hierarchy-table-body .row-cb:checked');
          if (checked.length === 0) return;
          const confirmed = await showConfirmModal(
            '🗑️ Confirm Bulk Deletion',
            `Are you sure you want to delete ${checked.length} selected node(s)? This action cannot be undone.`,
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
          const confirmTitle = isEdit ? '✏️ Confirm Save Edit' : '➕ Confirm Add Node';
          const confirmMsg = isEdit 
            ? 'Are you sure you want to save the changes to this node?' 
            : 'Are you sure you want to add this new node?';

          const confirmed = await showConfirmModal(confirmTitle, confirmMsg, 'Confirm & Save', '#16a34a');
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
            `Are you sure you want to delete node "${olt}" from ${c} (${rt})? This action cannot be undone.`,
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

        async function clearAllHierarchy() {
          if (!confirm('⚠️ WARNING: This will clear all uploaded network hierarchy data! Are you sure?')) return;
          try {
            await fetch('/api/hierarchy/clear', { method: 'DELETE' });
            fetchHierarchy();
          } catch(err) {
            alert('Error: ' + err.message);
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

        async function fetchUsers() {
          try {
            const res = await fetch('/api/users');
            const data = await res.json();
            const agentsEl = document.getElementById('active-agents');
            if (agentsEl) agentsEl.innerText = data.users.length;
            const tbody = document.getElementById('users-table-body');
            tbody.innerHTML = '';
            data.users.forEach(u => {
              const tr = document.createElement('tr');
              tr.innerHTML = `
                <td><strong>${u.username}</strong></td>
                <td>${u.full_name}</td>
                <td><span class="tag" style="background:#059669;">${u.assigned_center}</span></td>
                <td>${u.role}</td>
                <td>
                  ${u.username !== 'admin' ? `<button class="btn btn-danger" style="padding:4px 8px; font-size:0.75rem;" onclick="deleteUser('${u.username}')">Delete</button>` : '-'}
                </td>
              `;
              tbody.appendChild(tr);
            });
          } catch(e) {}
        }

        async function createUser(e) {
          e.preventDefault();
          const u = {
            username: document.getElementById('new-user').value.trim(),
            password: document.getElementById('new-pass').value.trim(),
            full_name: document.getElementById('new-name').value.trim(),
            assigned_center: document.getElementById('new-center').value,
            role: document.getElementById('new-center').value === 'ALL' ? 'admin' : 'field_agent'
          };
          await fetch('/api/users', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(u)
          });
          alert('User created / updated successfully!');
          document.getElementById('new-user').value = '';
          document.getElementById('new-pass').value = '';
          document.getElementById('new-name').value = '';
          fetchUsers();
        }

        async function deleteUser(u) {
          const confirmed = await showConfirmModal(
            '🗑️ Confirm User Deletion',
            `Are you sure you want to delete surveyor user "${u}"?`,
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

        fetchData();
        setInterval(fetchData, 12000);
      </script>
    </body>
    </html>
    """

# Mount static files for the mobile PWA web app
app.mount("/", StaticFiles(directory=WEB_APP_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=9001, reload=True)

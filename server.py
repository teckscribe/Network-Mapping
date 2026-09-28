"""
GPON Field Survey Central Server
FastAPI + SQLite backend with Offline-First Sync, User Authentication, Center Filtering, and Central Office Dashboard.
"""

import os
import sqlite3
import datetime
import socket
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_APP_DIR = os.path.join(BASE_DIR, "web_app")
DB_PATH = os.path.join(BASE_DIR, "gpon_survey_data.db")

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

    # Seed Default Users if none exist
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
        print("Default users initialized.")

    conn.commit()
    conn.close()

init_db()

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
    return {"status": "success", "message": f"User {username} deleted."}

# ====================
# NETWORK HIERARCHY API
# ====================

@app.get("/api/hierarchy")
def get_hierarchy():
    hierarchy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
    if os.path.exists(hierarchy_file):
        try:
            import json
            with open(hierarchy_file, "r", encoding="utf-8") as f:
                return {"hierarchy": json.load(f)}
        except Exception:
            pass
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
    hierarchy_file = os.path.join(BASE_DIR, "custom_hierarchy.json")
    try:
        import json
        with open(hierarchy_file, "w", encoding="utf-8") as f:
            json.dump(payload.get("hierarchy", payload), f, indent=2)
        return {"status": "success", "message": "Server network hierarchy updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

    headers = [
        'Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', ' OLT/Node  Name',
        'Port Number', 'KSEB Post Number', 'Land Mark', 'Enclosure Number',
        'Enclosure ID', 'Lat /Long', 'Splitter ID', 'Splitter Ratio',
        'No: Of Customer Connected', 'Splitter Lead Colour Code',
        'ADL Subscriber ID', 'ACS Subscriber ID', 'Date & Time', 'Surveyor Name'
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

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
            r['region'] or 'Thrissur',
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

    export_path = os.path.join(BASE_DIR, "GPON_Master_Server_Export.xlsx")
    wb.save(export_path)

    today = datetime.date.today().isoformat()
    return FileResponse(
        export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"GPON_Master_Network_Mapping_{today}.xlsx"
    )

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
        <div style="display:flex; gap:10px;">
          <a href="/api/export-excel" class="btn btn-green">📊 Download Master Excel</a>
          <button onclick="fetchData()" class="btn">🔄 Refresh</button>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-num" id="total-count">0</div>
          <div class="stat-label">Total Poles / Enclosures Synced</div>
        </div>
        <div class="stat-card">
          <div class="stat-num" id="total-customers">0</div>
          <div class="stat-label">Total Connected Customers</div>
        </div>
        <div class="stat-card">
          <div class="stat-num" id="active-agents">0</div>
          <div class="stat-label">Field Survey Users</div>
        </div>
      </div>

      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('feed')">📋 Survey Feed</button>
        <button class="tab-btn" onclick="switchTab('users')">👥 Field Users & Center Assignment</button>
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
                <th>OLT & Port</th>
                <th>KSEB Post #</th>
                <th>Landmark</th>
                <th>Lat / Long</th>
                <th>Cust</th>
                <th>Lead Color</th>
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
        <h3 style="margin-top:0; color:#0284c7;">Add / Manage Field Surveyors</h3>
        
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

      <script>
        function switchTab(t) {
          document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
          if (t === 'feed') {
            document.querySelectorAll('.tab-btn')[0].classList.add('active');
            document.getElementById('tab-feed').style.display = 'block';
            document.getElementById('tab-users').style.display = 'none';
          } else {
            document.querySelectorAll('.tab-btn')[1].classList.add('active');
            document.getElementById('tab-feed').style.display = 'none';
            document.getElementById('tab-users').style.display = 'block';
            fetchUsers();
          }
        }

        async function fetchData() {
          try {
            const res = await fetch('/api/records');
            const data = await res.json();
            document.getElementById('total-count').innerText = data.count;
            
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

            document.getElementById('total-customers').innerText = custTotal;
          } catch(e) {
            console.error(e);
          }
          fetchUsers();
        }

        async function fetchUsers() {
          try {
            const res = await fetch('/api/users');
            const data = await res.json();
            document.getElementById('active-agents').innerText = data.users.length;
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
          if (!confirm('Delete user ' + u + '?')) return;
          await fetch('/api/users/' + u, { method: 'DELETE' });
          fetchUsers();
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

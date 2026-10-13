# Server.py Line-by-Line & Route Wiring Dissection
*Generated automatically on: 2026-10-13 16:06:14*

Total lines in `server.py`: **7132** LOC

## 1. Subsystem Functional Blocks (Line-by-Line Breakdown)
The 6,229 lines of `server.py` are partitioned into 16 mission-critical functional blocks:

| Block | Line Range | Module / Feature Name | Core Functions / Responsibilities |
| :--- | :--- | :--- | :--- |
| **Block 1** | L1 - L81 | **Imports, Paths & SMTP Configuration** | Dynamically loads SMTP settings from `.env`, defines system paths (`DB_PATH`, `WEB_APP_DIR`, `NODE_MASTER_DIR`). Function: `load_smtp_config()`. |
| **Block 2** | L82 - L135 | **Cryptographic Security & Password Hashing** | PBKDF2/HMAC-SHA256 salted password hashing and timing-safe verification. Functions: `hash_password()`, `verify_password()`, `is_hashed()`. |
| **Block 3** | L136 - L245 | **Session Tokens & 5-Tier RBAC Gatekeepers** | Generates HMAC-signed session tokens with 24h validity. Enforces 5 tiers: `require_super_admin_auth()`, `require_admin_auth()`, `require_management_auth()`, `require_export_auth()`, `require_any_auth()`. |
| **Block 4** | L246 - L280 | **IP Extraction & Rate Limiting** | Extracts real client IP behind reverse proxies/Tailscale. Enforces sliding window rate limits. Functions: `get_client_ip()`, `check_rate_limit()`. |
| **Block 5** | L281 - L378 | **User Persistence & Normalization** | Normalizes roles (`super_admin`, `admin`, `rcsm`, `acso`, `field_technician`). Dual-syncs credentials between SQLite and `users_config.json`. Functions: `normalize_role()`, `save_users_to_json()`, `load_users_from_json()`. |
| **Block 6** | L379 - L528 | **SQLite Database Initialization & Migrations** | Executes `CREATE TABLE` and conditional `ALTER TABLE` migrations for `survey_records`, `users`, and `password_reset_otps`. Creates composite indexes. Function: `init_db()`. |
| **Block 7** | L529 - L675 | **Node Master Hierarchy & Jurisdiction Engine** | Reads and normalizes 4-tier tree (`Region -> Center -> RT Room -> OLT -> Ports`). Computes regional scopes for RCSM and ACSO users. Functions: `load_hierarchy_data()`, `save_hierarchy_data()`, `get_region_for_center()`, `get_user_jurisdiction()`. |
| **Block 8** | L676 - L755 | **In-Memory Excel Streaming Engine** | Builds styled 24-column Excel workbooks in RAM with `openpyxl`. Converts workbooks to byte buffers. Functions: `build_excel_workbook()`, `workbook_to_bytes()`. |
| **Block 9** | L756 - L805 | **Storage Optimization & Directory Cleanup** | Cleans up legacy disk spreadsheets and guarantees zero static file footprint. Function: `ensure_directories_and_migrate()`. |
| **Block 10** | L806 - L935 | **Pydantic Validation Models & Server Enclosure Calculator** | Validates incoming payloads (`SurveyRecordModel`, `SyncPayload`, `UserCreateModel`, `OLTEditModel`). Server-side enclosure ID recalculation: `compute_server_enclosure_id()`. |
| **Block 11** | L936 - L1785 | **Auth & User Management REST Endpoints** | Endpoints: `/api/login`, `/api/logout`, `/api/user-profile`, `/api/users` (CRUD), `/api/upload-users-excel`, `/api/request-password-reset-otp`, `/api/verify-password-reset-otp`, `/api/change-password`. |
| **Block 12** | L1786 - L2165 | **Node Master Hierarchy REST Endpoints** | Endpoints: `/api/hierarchy` (GET), `/api/upload-hierarchy` (POST), `/api/upload-hierarchy-excel` (POST), `/api/hierarchy/olt` (POST/DELETE), `/api/hierarchy/bulk-delete` (POST), `/api/hierarchy/clear` (DELETE). |
| **Block 13** | L2166 - L2685 | **Survey Records & Sync REST Endpoints** | Endpoints: `/api/health`, `/api/surveyed-points` (GET), `/api/sync` (POST), `/api/records` (GET), `/api/records/{client_uuid}` (PUT/DELETE), `/api/records/bulk-delete` (POST), `/api/records/clear-center` (POST). |
| **Block 14** | L2686 - L3130 | **Streaming Export & Data Summary Endpoints** | Endpoints: `/api/export-excel` (GET), `/api/export-center-excel` (GET), `/api/export-data-zip` (GET), `/api/upload-survey-excel` (POST), `/api/data-folders-summary` (GET), `/api/resync-data-folders` (POST). |
| **Block 15** | L3131 - L6222 | **Central Office Admin Web Portal (Single-Page App)** | Massive embedded HTML/JS dashboard at `/admin`. Includes Live Survey Feed, Filter Controls, Live Leaflet Map, User Management modal, Hierarchy Editor, Splitter Badges, and Master Export buttons. |
| **Block 16** | L6223 - L6230 | **Static Mount & ASGI Application Launch** | Mounts `web_app/` as static files at root `/`. Starts Uvicorn server on port `9001` with auto-reload. |

## 2. Complete REST API Route Registry

| HTTP Method | Route Path | Line Range | Handler Function | Security & Role Gate | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/login` | L985-L1068 | `login()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/logout` | L1071-L1073 | `api_logout()` | `Public / Session` | Stateless session logout endpoint. |
| `GET` | `/api/user-profile` | L1076-L1109 | `get_user_profile()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/users` | L1112-L1133 | `get_users()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/users` | L1136-L1205 | `create_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-users-template` | L1208-L1286 | `download_users_template()` | `Public / Session` | Generates an institutional Excel template for bulk user provisioning with access tiers. |
| `POST` | `/api/upload-users-excel` | L1289-L1443 | `upload_users_excel()` | `require_admin_auth` | Bulk uploads user credentials and access rights from an Excel (.xlsx/.xls) or CSV file. |
| `POST` | `/api/request-password-reset-otp` | L1540-L1626 | `request_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/test-smtp` | L1629-L1725 | `test_smtp_endpoint()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/verify-password-reset-otp` | L1728-L1781 | `verify_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/change-password` | L1784-L1830 | `change_password()` | `require_any_auth` | REST Endpoint |
| `DELETE` | `/api/users/{username}` | L1833-L1851 | `delete_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/config/users` | L1854-L1858 | `export_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/config/users` | L1861-L1873 | `import_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/hierarchy` | L1880-L1898 | `get_hierarchy()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/upload-hierarchy` | L1901-L1935 | `upload_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/upload-hierarchy-excel` | L1938-L2084 | `upload_hierarchy_excel()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/olt` | L2087-L2139 | `save_or_edit_olt()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/olt` | L2142-L2162 | `delete_olt()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/bulk-delete` | L2165-L2189 | `bulk_delete_olts()` | `require_admin_auth` | Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]} |
| `DELETE` | `/api/hierarchy/clear` | L2192-L2194 | `clear_hierarchy()` | `require_super_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/center` | L2197-L2210 | `delete_hierarchy_center()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/download-project-ppt` | L2212-L2220 | `download_project_ppt()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/backup-status` | L2223-L2251 | `get_backup_status()` | `Public / Session` | Decoupled zero-overhead backup status reporter. |
| `GET` | `/api/download-hierarchy-template` | L2254-L2291 | `download_hierarchy_template()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/health` | L2298-L2304 | `health_check()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/surveyed-points` | L2340-L2456 | `get_surveyed_points()` | `require_any_auth` | Returns an index map of all surveyed enclosure/splitter points across the network. |
| `POST` | `/api/sync` | L2459-L2630 | `sync_records()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/records` | L2634-L2697 | `get_all_records()` | `require_management_auth` | REST Endpoint |
| `PUT` | `/api/records/{client_uuid}` | L2700-L2761 | `update_survey_record()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/records/{client_uuid}` | L2764-L2804 | `delete_record()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/bulk-delete` | L2807-L2830 | `bulk_delete_records()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/clear-center` | L2833-L2852 | `clear_center_records()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/export-excel` | L2855-L2893 | `export_server_excel()` | `require_management_auth` | REST Endpoint |
| `GET` | `/api/export-center-excel` | L2896-L2981 | `export_center_excel()` | `require_export_auth` | Exports and downloads filtered survey Excel file streamed dynamically in-memory. |
| `GET` | `/api/export-data-zip` | L2984-L3056 | `export_data_zip()` | `require_management_auth` | Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory. |
| `POST` | `/api/upload-survey-excel` | L3059-L3213 | `upload_survey_excel()` | `require_admin_auth` | Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory. |
| `GET` | `/api/data-folders-summary` | L3216-L3298 | `get_data_folders_summary()` | `require_management_auth` | Returns structured summary of region and center survey records with dynamic in-memory exports. |
| `POST` | `/api/resync-data-folders` | L3301-L3309 | `resync_data_folders()` | `require_admin_auth` | Optimizes server storage and recounts SQLite records. |
| `GET` | `/docs/wiring` | L3314-L3319 | `get_wiring_report()` | `Public / Session` | Serves the interactive architecture wiring and blast-radius report. |
| `GET` | `/manifest.json` | L3322-L3334 | `get_pwa_manifest()` | `Public / Session` | REST Endpoint |
| `GET` | `/admin` | L3338-L7124 | `admin_dashboard()` | `Public / Session` | REST Endpoint |

## 3. SQLite Database Query Registry
| Line | Target Table | SQL Operation Snippet |
| :--- | :--- | :--- |
| L317 | `users` | `SELECT username, password, full_name, assigned_center, assigned_region, role, created_a...` |
| L391 | `unknown` | `PRAGMA journal_mode = WAL;` |
| L392 | `unknown` | `PRAGMA busy_timeout = 10000;` |
| L393 | `unknown` | `PRAGMA synchronous = NORMAL;` |
| L453 | `unknown` | `PRAGMA table_info(users)` |
| L456 | `users` | `ALTER TABLE users ADD COLUMN phone TEXT DEFAULT` |
| L458 | `users` | `ALTER TABLE users ADD COLUMN email TEXT DEFAULT` |
| L460 | `users` | `ALTER TABLE users ADD COLUMN assigned_region TEXT DEFAULT` |
| L462 | `users` | `ALTER TABLE users ADD COLUMN role TEXT DEFAULT` |
| L464 | `users` | `ALTER TABLE users ADD COLUMN created_at TEXT` |
| L465 | `users` | `UPDATE users SET phone =` |
| L466 | `users` | `UPDATE users SET email =` |
| L467 | `users` | `UPDATE users SET assigned_region =` |
| L468 | `users` | `UPDATE users SET role =` |
| L471 | `unknown` | `PRAGMA table_info(survey_records)` |
| L474 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_username TEXT` |
| L476 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_name TEXT` |
| L478 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN splitter_lead_color TEXT` |
| L480 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN adl_subscriber_id TEXT` |
| L482 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN acs_subscriber_id TEXT` |
| L484 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN survey_date_time TEXT` |
| L486 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center)` |
| L487 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id)` |
| L488 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enc_spl ON survey_records(enclosure_id, splitter...` |
| L525 | `users` | `SELECT username FROM users WHERE username =` |
| L534 | `users` | `UPDATE users SET role =` |
| L537 | `users` | `SELECT username, password FROM users` |
| L541 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L551 | `users` | `SELECT username, assigned_center FROM users` |
| L558 | `users` | `UPDATE users SET assigned_center = ? WHERE username = ?` |
| L668 | `users` | `SELECT role, assigned_region, assigned_center FROM users WHERE LOWER(TRIM(username)) = ...` |
| L1034 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1085 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1116 | `users` | `SELECT username, full_name, phone, email, assigned_center, assigned_region, role, creat...` |
| L1168 | `users` | `SELECT password, role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1388 | `users` | `SELECT password, assigned_center, assigned_region, phone, role FROM users WHERE LOWER(T...` |
| L1603 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1742 | `password_reset_otps` | `SELECT * FROM password_reset_otps WHERE username = ?` |
| L1750 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1756 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1763 | `password_reset_otps` | `UPDATE password_reset_otps SET attempts = ? WHERE username = ?` |
| L1770 | `users` | `UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?` |
| L1771 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1804 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1825 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1837 | `users` | `SELECT role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1847 | `users` | `DELETE FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L2617 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
| L2703 | `survey_records` | `SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id ...` |
| L2776 | `survey_records` | `DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)` |
| L2842 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(re...` |
| L2844 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))` |
| L3222 | `survey_records` | `SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center` |
| L3306 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
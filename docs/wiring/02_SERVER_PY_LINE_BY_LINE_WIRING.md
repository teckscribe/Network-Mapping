# Server.py Line-by-Line & Route Wiring Dissection
*Generated automatically on: 2026-10-10 17:26:44*

Total lines in `server.py`: **6240** LOC

## 1. Subsystem Functional Blocks (Line-by-Line Breakdown)
The 6,229 lines of `server.py` are partitioned into 16 mission-critical functional blocks:

| Block | Line Range | Module / Feature Name | Core Functions / Responsibilities |
| :--- | :--- | :--- | :--- |
| **Block 1** | L1 - L81 | **Imports, Paths & SMTP Configuration** | Dynamically loads SMTP settings from `.env`, defines system paths (`DB_PATH`, `WEB_APP_DIR`, `NODE_MASTER_DIR`). Function: `load_smtp_config()`. |
| **Block 2** | L82 - L135 | **Cryptographic Security & Password Hashing** | PBKDF2/HMAC-SHA256 salted password hashing and timing-safe verification. Functions: `hash_password()`, `verify_password()`, `is_hashed()`. |
| **Block 3** | L136 - L245 | **Session Tokens & 4-Tier RBAC Gatekeepers** | Generates HMAC-signed session tokens with 24h validity. Enforces 4 tiers: `require_admin_auth()`, `require_management_auth()`, `require_export_auth()`, `require_any_auth()`. |
| **Block 4** | L246 - L280 | **IP Extraction & Rate Limiting** | Extracts real client IP behind reverse proxies/Tailscale. Enforces sliding window rate limits. Functions: `get_client_ip()`, `check_rate_limit()`. |
| **Block 5** | L281 - L378 | **User Persistence & Normalization** | Normalizes roles (`super_admin`, `rcsm`, `acso`, `field_technician`). Dual-syncs credentials between SQLite and `users_config.json`. Functions: `normalize_role()`, `save_users_to_json()`, `load_users_from_json()`. |
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
| `POST` | `/api/login` | L939-L1022 | `login()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/logout` | L1025-L1027 | `api_logout()` | `Public / Session` | Stateless session logout endpoint. |
| `GET` | `/api/user-profile` | L1030-L1063 | `get_user_profile()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/users` | L1066-L1087 | `get_users()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/users` | L1090-L1144 | `create_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-users-template` | L1147-L1224 | `download_users_template()` | `Public / Session` | Generates an institutional Excel template for bulk user provisioning with access tiers. |
| `POST` | `/api/upload-users-excel` | L1227-L1371 | `upload_users_excel()` | `require_admin_auth` | Bulk uploads user credentials and access rights from an Excel (.xlsx/.xls) or CSV file. |
| `POST` | `/api/request-password-reset-otp` | L1468-L1554 | `request_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/test-smtp` | L1557-L1653 | `test_smtp_endpoint()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/verify-password-reset-otp` | L1656-L1709 | `verify_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/change-password` | L1712-L1751 | `change_password()` | `require_any_auth` | REST Endpoint |
| `DELETE` | `/api/users/{username}` | L1754-L1763 | `delete_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/config/users` | L1766-L1770 | `export_users_config()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/config/users` | L1773-L1785 | `import_users_config()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/hierarchy` | L1792-L1810 | `get_hierarchy()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/upload-hierarchy` | L1813-L1847 | `upload_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/upload-hierarchy-excel` | L1850-L1996 | `upload_hierarchy_excel()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/olt` | L1999-L2051 | `save_or_edit_olt()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/olt` | L2054-L2074 | `delete_olt()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/bulk-delete` | L2077-L2101 | `bulk_delete_olts()` | `require_admin_auth` | Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]} |
| `DELETE` | `/api/hierarchy/clear` | L2104-L2106 | `clear_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/center` | L2109-L2123 | `delete_hierarchy_center()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-hierarchy-template` | L2126-L2163 | `download_hierarchy_template()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/health` | L2170-L2176 | `health_check()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/surveyed-points` | L2200-L2316 | `get_surveyed_points()` | `require_any_auth` | Returns an index map of all surveyed enclosure/splitter points across the network. |
| `POST` | `/api/sync` | L2319-L2472 | `sync_records()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/records` | L2476-L2540 | `get_all_records()` | `require_management_auth` | REST Endpoint |
| `PUT` | `/api/records/{client_uuid}` | L2543-L2592 | `update_survey_record()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/records/{client_uuid}` | L2595-L2635 | `delete_record()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/bulk-delete` | L2638-L2661 | `bulk_delete_records()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/clear-center` | L2664-L2683 | `clear_center_records()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/export-excel` | L2686-L2723 | `export_server_excel()` | `require_management_auth` | REST Endpoint |
| `GET` | `/api/export-center-excel` | L2726-L2811 | `export_center_excel()` | `require_export_auth` | Exports and downloads filtered survey Excel file streamed dynamically in-memory. |
| `GET` | `/api/export-data-zip` | L2814-L2885 | `export_data_zip()` | `require_management_auth` | Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory. |
| `POST` | `/api/upload-survey-excel` | L2888-L3033 | `upload_survey_excel()` | `require_admin_auth` | Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory. |
| `GET` | `/api/data-folders-summary` | L3036-L3118 | `get_data_folders_summary()` | `require_management_auth` | Returns structured summary of region and center survey records with dynamic in-memory exports. |
| `POST` | `/api/resync-data-folders` | L3121-L3129 | `resync_data_folders()` | `require_admin_auth` | Optimizes server storage and recounts SQLite records. |
| `GET` | `/docs/wiring` | L3134-L3139 | `get_wiring_report()` | `Public / Session` | Serves the interactive architecture wiring and blast-radius report. |
| `GET` | `/admin` | L3143-L6232 | `admin_dashboard()` | `Public / Session` | REST Endpoint |

## 3. SQLite Database Query Registry
| Line | Target Table | SQL Operation Snippet |
| :--- | :--- | :--- |
| L308 | `users` | `SELECT username, password, full_name, assigned_center, assigned_region, role, created_a...` |
| L382 | `unknown` | `PRAGMA journal_mode = WAL;` |
| L383 | `unknown` | `PRAGMA busy_timeout = 10000;` |
| L384 | `unknown` | `PRAGMA synchronous = NORMAL;` |
| L444 | `unknown` | `PRAGMA table_info(users)` |
| L447 | `users` | `ALTER TABLE users ADD COLUMN phone TEXT DEFAULT` |
| L449 | `users` | `ALTER TABLE users ADD COLUMN email TEXT DEFAULT` |
| L451 | `users` | `ALTER TABLE users ADD COLUMN assigned_region TEXT DEFAULT` |
| L453 | `users` | `ALTER TABLE users ADD COLUMN role TEXT DEFAULT` |
| L455 | `users` | `ALTER TABLE users ADD COLUMN created_at TEXT` |
| L456 | `users` | `UPDATE users SET phone =` |
| L457 | `users` | `UPDATE users SET email =` |
| L458 | `users` | `UPDATE users SET assigned_region =` |
| L459 | `users` | `UPDATE users SET role =` |
| L462 | `unknown` | `PRAGMA table_info(survey_records)` |
| L465 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_username TEXT` |
| L467 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_name TEXT` |
| L469 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN splitter_lead_color TEXT` |
| L471 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN adl_subscriber_id TEXT` |
| L473 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN acs_subscriber_id TEXT` |
| L475 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN survey_date_time TEXT` |
| L477 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center)` |
| L478 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id)` |
| L479 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enc_spl ON survey_records(enclosure_id, splitter...` |
| L489 | `users` | `SELECT username FROM users WHERE username =` |
| L498 | `users` | `UPDATE users SET role =` |
| L499 | `users` | `UPDATE users SET role =` |
| L502 | `users` | `SELECT username, password FROM users` |
| L506 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L516 | `users` | `SELECT username, assigned_center FROM users` |
| L523 | `users` | `UPDATE users SET assigned_center = ? WHERE username = ?` |
| L633 | `users` | `SELECT role, assigned_region, assigned_center FROM users WHERE LOWER(TRIM(username)) = ...` |
| L988 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1039 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1070 | `users` | `SELECT username, full_name, phone, email, assigned_center, assigned_region, role, creat...` |
| L1115 | `users` | `SELECT password FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1321 | `users` | `SELECT password, assigned_center, assigned_region, phone FROM users WHERE LOWER(TRIM(us...` |
| L1531 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1670 | `password_reset_otps` | `SELECT * FROM password_reset_otps WHERE username = ?` |
| L1678 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1684 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1691 | `password_reset_otps` | `UPDATE password_reset_otps SET attempts = ? WHERE username = ?` |
| L1698 | `users` | `UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?` |
| L1699 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1730 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1746 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1759 | `users` | `DELETE FROM users WHERE username = ?` |
| L2459 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
| L2546 | `survey_records` | `SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id ...` |
| L2607 | `survey_records` | `DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)` |
| L2673 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(re...` |
| L2675 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))` |
| L3042 | `survey_records` | `SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center` |
| L3126 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
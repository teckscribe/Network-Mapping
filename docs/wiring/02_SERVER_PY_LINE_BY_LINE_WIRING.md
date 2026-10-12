# Server.py Line-by-Line & Route Wiring Dissection
*Generated automatically on: 2026-10-12 11:10:35*

Total lines in `server.py`: **6886** LOC

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
| `POST` | `/api/login` | L963-L1046 | `login()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/logout` | L1049-L1051 | `api_logout()` | `Public / Session` | Stateless session logout endpoint. |
| `GET` | `/api/user-profile` | L1054-L1087 | `get_user_profile()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/users` | L1090-L1111 | `get_users()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/users` | L1114-L1183 | `create_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-users-template` | L1186-L1264 | `download_users_template()` | `Public / Session` | Generates an institutional Excel template for bulk user provisioning with access tiers. |
| `POST` | `/api/upload-users-excel` | L1267-L1421 | `upload_users_excel()` | `require_admin_auth` | Bulk uploads user credentials and access rights from an Excel (.xlsx/.xls) or CSV file. |
| `POST` | `/api/request-password-reset-otp` | L1518-L1604 | `request_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/test-smtp` | L1607-L1703 | `test_smtp_endpoint()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/verify-password-reset-otp` | L1706-L1759 | `verify_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/change-password` | L1762-L1808 | `change_password()` | `require_any_auth` | REST Endpoint |
| `DELETE` | `/api/users/{username}` | L1811-L1829 | `delete_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/config/users` | L1832-L1836 | `export_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/config/users` | L1839-L1851 | `import_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/hierarchy` | L1858-L1876 | `get_hierarchy()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/upload-hierarchy` | L1879-L1913 | `upload_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/upload-hierarchy-excel` | L1916-L2062 | `upload_hierarchy_excel()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/olt` | L2065-L2117 | `save_or_edit_olt()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/olt` | L2120-L2140 | `delete_olt()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/bulk-delete` | L2143-L2167 | `bulk_delete_olts()` | `require_admin_auth` | Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]} |
| `DELETE` | `/api/hierarchy/clear` | L2170-L2172 | `clear_hierarchy()` | `require_super_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/center` | L2175-L2189 | `delete_hierarchy_center()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/download-hierarchy-template` | L2192-L2229 | `download_hierarchy_template()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/health` | L2236-L2242 | `health_check()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/surveyed-points` | L2266-L2382 | `get_surveyed_points()` | `require_any_auth` | Returns an index map of all surveyed enclosure/splitter points across the network. |
| `POST` | `/api/sync` | L2385-L2538 | `sync_records()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/records` | L2542-L2606 | `get_all_records()` | `require_management_auth` | REST Endpoint |
| `PUT` | `/api/records/{client_uuid}` | L2609-L2658 | `update_survey_record()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/records/{client_uuid}` | L2661-L2701 | `delete_record()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/bulk-delete` | L2704-L2727 | `bulk_delete_records()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/clear-center` | L2730-L2749 | `clear_center_records()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/export-excel` | L2752-L2790 | `export_server_excel()` | `require_management_auth` | REST Endpoint |
| `GET` | `/api/export-center-excel` | L2793-L2878 | `export_center_excel()` | `require_export_auth` | Exports and downloads filtered survey Excel file streamed dynamically in-memory. |
| `GET` | `/api/export-data-zip` | L2881-L2953 | `export_data_zip()` | `require_management_auth` | Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory. |
| `POST` | `/api/upload-survey-excel` | L2956-L3101 | `upload_survey_excel()` | `require_admin_auth` | Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory. |
| `GET` | `/api/data-folders-summary` | L3104-L3186 | `get_data_folders_summary()` | `require_management_auth` | Returns structured summary of region and center survey records with dynamic in-memory exports. |
| `POST` | `/api/resync-data-folders` | L3189-L3197 | `resync_data_folders()` | `require_admin_auth` | Optimizes server storage and recounts SQLite records. |
| `GET` | `/api/backup-status` | L3355-L3405 | `get_backup_status()` | `require_management_auth` | Returns Google Drive backup status, latest sync time, relative time, and recent logs. |
| `POST` | `/api/trigger-backup` | L3408-L3418 | `trigger_backup()` | `require_admin_auth` | Triggers an asynchronous backup and cloud sync to Google Drive (Admin only). |
| `GET` | `/docs/wiring` | L3423-L3428 | `get_wiring_report()` | `Public / Session` | Serves the interactive architecture wiring and blast-radius report. |
| `GET` | `/admin` | L3432-L6878 | `admin_dashboard()` | `Public / Session` | REST Endpoint |

## 3. SQLite Database Query Registry
| Line | Target Table | SQL Operation Snippet |
| :--- | :--- | :--- |
| L322 | `users` | `SELECT username, password, full_name, assigned_center, assigned_region, role, created_a...` |
| L396 | `unknown` | `PRAGMA journal_mode = WAL;` |
| L397 | `unknown` | `PRAGMA busy_timeout = 10000;` |
| L398 | `unknown` | `PRAGMA synchronous = NORMAL;` |
| L458 | `unknown` | `PRAGMA table_info(users)` |
| L461 | `users` | `ALTER TABLE users ADD COLUMN phone TEXT DEFAULT` |
| L463 | `users` | `ALTER TABLE users ADD COLUMN email TEXT DEFAULT` |
| L465 | `users` | `ALTER TABLE users ADD COLUMN assigned_region TEXT DEFAULT` |
| L467 | `users` | `ALTER TABLE users ADD COLUMN role TEXT DEFAULT` |
| L469 | `users` | `ALTER TABLE users ADD COLUMN created_at TEXT` |
| L470 | `users` | `UPDATE users SET phone =` |
| L471 | `users` | `UPDATE users SET email =` |
| L472 | `users` | `UPDATE users SET assigned_region =` |
| L473 | `users` | `UPDATE users SET role =` |
| L476 | `unknown` | `PRAGMA table_info(survey_records)` |
| L479 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_username TEXT` |
| L481 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_name TEXT` |
| L483 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN splitter_lead_color TEXT` |
| L485 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN adl_subscriber_id TEXT` |
| L487 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN acs_subscriber_id TEXT` |
| L489 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN survey_date_time TEXT` |
| L491 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center)` |
| L492 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id)` |
| L493 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enc_spl ON survey_records(enclosure_id, splitter...` |
| L503 | `users` | `SELECT username FROM users WHERE username =` |
| L512 | `users` | `UPDATE users SET role =` |
| L515 | `users` | `SELECT username, password FROM users` |
| L519 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L529 | `users` | `SELECT username, assigned_center FROM users` |
| L536 | `users` | `UPDATE users SET assigned_center = ? WHERE username = ?` |
| L646 | `users` | `SELECT role, assigned_region, assigned_center FROM users WHERE LOWER(TRIM(username)) = ...` |
| L1012 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1063 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1094 | `users` | `SELECT username, full_name, phone, email, assigned_center, assigned_region, role, creat...` |
| L1146 | `users` | `SELECT password, role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1366 | `users` | `SELECT password, assigned_center, assigned_region, phone, role FROM users WHERE LOWER(T...` |
| L1581 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1720 | `password_reset_otps` | `SELECT * FROM password_reset_otps WHERE username = ?` |
| L1728 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1734 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1741 | `password_reset_otps` | `UPDATE password_reset_otps SET attempts = ? WHERE username = ?` |
| L1748 | `users` | `UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?` |
| L1749 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1782 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1803 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1815 | `users` | `SELECT role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1825 | `users` | `DELETE FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L2525 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
| L2612 | `survey_records` | `SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id ...` |
| L2673 | `survey_records` | `DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)` |
| L2739 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(re...` |
| L2741 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))` |
| L3110 | `survey_records` | `SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center` |
| L3194 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
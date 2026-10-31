# Server.py Line-by-Line & Route Wiring Dissection
*Generated automatically on: 2026-10-31 23:54:09*

Total lines in `server.py`: **8177** LOC

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
| `POST` | `/api/login` | L1003-L1086 | `login()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/logout` | L1089-L1091 | `api_logout()` | `Public / Session` | Stateless session logout endpoint. |
| `GET` | `/api/user-profile` | L1094-L1127 | `get_user_profile()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/users` | L1130-L1151 | `get_users()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/users` | L1154-L1223 | `create_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-users-template` | L1226-L1304 | `download_users_template()` | `Public / Session` | Generates an institutional Excel template for bulk user provisioning with access tiers. |
| `POST` | `/api/upload-users-excel` | L1307-L1461 | `upload_users_excel()` | `require_admin_auth` | Bulk uploads user credentials and access rights from an Excel (.xlsx/.xls) or CSV file. |
| `POST` | `/api/request-password-reset-otp` | L1558-L1644 | `request_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/test-smtp` | L1647-L1743 | `test_smtp_endpoint()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/verify-password-reset-otp` | L1746-L1799 | `verify_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/change-password` | L1802-L1848 | `change_password()` | `require_any_auth` | REST Endpoint |
| `DELETE` | `/api/users/{username}` | L1851-L1869 | `delete_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/config/users` | L1872-L1876 | `export_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/config/users` | L1879-L1891 | `import_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/hierarchy` | L1898-L1916 | `get_hierarchy()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/upload-hierarchy` | L1919-L1953 | `upload_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/upload-hierarchy-excel` | L1956-L2102 | `upload_hierarchy_excel()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/olt` | L2105-L2157 | `save_or_edit_olt()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/olt` | L2160-L2180 | `delete_olt()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/bulk-delete` | L2183-L2207 | `bulk_delete_olts()` | `require_admin_auth` | Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]} |
| `DELETE` | `/api/hierarchy/clear` | L2210-L2212 | `clear_hierarchy()` | `require_super_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/center` | L2215-L2228 | `delete_hierarchy_center()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/download-project-ppt` | L2230-L2238 | `download_project_ppt()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/backup-status` | L2250-L2287 | `get_backup_status()` | `Public / Session` | Decoupled zero-overhead backup status reporter. |
| `GET` | `/api/backup/config` | L2290-L2308 | `get_backup_config()` | `require_admin_auth` | Returns current Google Cloud Backup settings (masked password) and recent backup log entries. |
| `POST` | `/api/backup/config` | L2311-L2365 | `save_backup_config_endpoint()` | `require_super_admin_auth` | Saves Google Cloud Backup credentials to .env, tests TLS login, and updates backup health status. |
| `POST` | `/api/backup/run-now` | L2368-L2393 | `run_backup_now_endpoint()` | `require_super_admin_auth` | Triggers an immediate hot SQLite snapshot + .tar.gz archive + Google Cloud upload. |
| `GET` | `/api/backup/download-latest` | L2396-L2410 | `download_latest_backup_archive()` | `require_admin_auth` | Downloads the latest gpon_backup_*.tar.gz archive (or creates one on the fly if none exists). |
| `GET` | `/api/download-hierarchy-template` | L2413-L2450 | `download_hierarchy_template()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/health` | L2457-L2463 | `health_check()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/surveyed-points` | L2499-L2615 | `get_surveyed_points()` | `require_any_auth` | Returns an index map of all surveyed enclosure/splitter points across the network. |
| `POST` | `/api/sync` | L2618-L2791 | `sync_records()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/records` | L2795-L2858 | `get_all_records()` | `require_management_auth` | REST Endpoint |
| `PUT` | `/api/records/{client_uuid}` | L2861-L2926 | `update_survey_record()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/records/{client_uuid}` | L2929-L2969 | `delete_record()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/bulk-delete` | L2972-L2995 | `bulk_delete_records()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/clear-center` | L2998-L3017 | `clear_center_records()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/export-excel` | L3020-L3058 | `export_server_excel()` | `require_management_auth` | REST Endpoint |
| `GET` | `/api/export-center-excel` | L3061-L3146 | `export_center_excel()` | `require_export_auth` | Exports and downloads filtered survey Excel file streamed dynamically in-memory. |
| `GET` | `/api/export-data-zip` | L3149-L3221 | `export_data_zip()` | `require_management_auth` | Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory. |
| `GET` | `/api/reports/catalog` | L3235-L3237 | `api_reports_catalog()` | `require_export_auth` | Returns the catalog of available reports, their formats, categories, and descriptions. |
| `GET` | `/api/reports/download/{report_type}` | L3240-L3287 | `api_download_report()` | `require_export_auth` | Generates and downloads any of the enterprise reports: |
| `POST` | `/api/upload-survey-excel` | L3290-L3449 | `upload_survey_excel()` | `require_admin_auth` | Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory. |
| `GET` | `/api/data-folders-summary` | L3452-L3534 | `get_data_folders_summary()` | `require_management_auth` | Returns structured summary of region and center survey records with dynamic in-memory exports. |
| `POST` | `/api/resync-data-folders` | L3537-L3545 | `resync_data_folders()` | `require_admin_auth` | Optimizes server storage and recounts SQLite records. |
| `GET` | `/docs/wiring` | L3550-L3555 | `get_wiring_report()` | `Public / Session` | Serves the interactive architecture wiring and blast-radius report. |
| `GET` | `/manifest.json` | L3558-L3570 | `get_pwa_manifest()` | `Public / Session` | REST Endpoint |
| `GET` | `/admin` | L3574-L8169 | `admin_dashboard()` | `Public / Session` | REST Endpoint |

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
| L1052 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1103 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1134 | `users` | `SELECT username, full_name, phone, email, assigned_center, assigned_region, role, creat...` |
| L1186 | `users` | `SELECT password, role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1406 | `users` | `SELECT password, assigned_center, assigned_region, phone, role FROM users WHERE LOWER(T...` |
| L1621 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1760 | `password_reset_otps` | `SELECT * FROM password_reset_otps WHERE username = ?` |
| L1768 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1774 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1781 | `password_reset_otps` | `UPDATE password_reset_otps SET attempts = ? WHERE username = ?` |
| L1788 | `users` | `UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?` |
| L1789 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1822 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1843 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1855 | `users` | `SELECT role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1865 | `users` | `DELETE FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L2778 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
| L2864 | `survey_records` | `SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id ...` |
| L2941 | `survey_records` | `DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)` |
| L3007 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(re...` |
| L3009 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))` |
| L3458 | `survey_records` | `SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center` |
| L3542 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
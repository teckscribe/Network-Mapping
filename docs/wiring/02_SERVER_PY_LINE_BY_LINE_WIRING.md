# Server.py Line-by-Line & Route Wiring Dissection
*Generated automatically on: 2026-11-01 00:14:33*

Total lines in `server.py`: **8561** LOC

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
| `POST` | `/api/login` | L1032-L1115 | `login()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/logout` | L1118-L1120 | `api_logout()` | `Public / Session` | Stateless session logout endpoint. |
| `GET` | `/api/user-profile` | L1123-L1156 | `get_user_profile()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/users` | L1159-L1180 | `get_users()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/users` | L1183-L1252 | `create_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/download-users-template` | L1255-L1333 | `download_users_template()` | `Public / Session` | Generates an institutional Excel template for bulk user provisioning with access tiers. |
| `POST` | `/api/upload-users-excel` | L1336-L1490 | `upload_users_excel()` | `require_admin_auth` | Bulk uploads user credentials and access rights from an Excel (.xlsx/.xls) or CSV file. |
| `POST` | `/api/request-password-reset-otp` | L1587-L1673 | `request_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/test-smtp` | L1676-L1772 | `test_smtp_endpoint()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/verify-password-reset-otp` | L1775-L1828 | `verify_password_reset_otp()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/change-password` | L1831-L1877 | `change_password()` | `require_any_auth` | REST Endpoint |
| `DELETE` | `/api/users/{username}` | L1880-L1898 | `delete_user()` | `require_admin_auth` | REST Endpoint |
| `GET` | `/api/config/users` | L1901-L1905 | `export_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `POST` | `/api/config/users` | L1908-L1920 | `import_users_config()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/hierarchy` | L1927-L1945 | `get_hierarchy()` | `Public / Session` | REST Endpoint |
| `POST` | `/api/upload-hierarchy` | L1948-L1982 | `upload_hierarchy()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/upload-hierarchy-excel` | L1985-L2131 | `upload_hierarchy_excel()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/olt` | L2134-L2186 | `save_or_edit_olt()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/olt` | L2189-L2209 | `delete_olt()` | `require_admin_auth` | REST Endpoint |
| `POST` | `/api/hierarchy/bulk-delete` | L2212-L2236 | `bulk_delete_olts()` | `require_admin_auth` | Delete multiple Nodes at once. Expects {"items": [{"center":..., "rt_room":..., "olt_name":...}, ...]} |
| `DELETE` | `/api/hierarchy/clear` | L2239-L2241 | `clear_hierarchy()` | `require_super_admin_auth` | REST Endpoint |
| `DELETE` | `/api/hierarchy/center` | L2244-L2257 | `delete_hierarchy_center()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/download-project-ppt` | L2259-L2267 | `download_project_ppt()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/backup-status` | L2279-L2316 | `get_backup_status()` | `Public / Session` | Decoupled zero-overhead backup status reporter. |
| `GET` | `/api/backup/config` | L2319-L2341 | `get_backup_config()` | `require_admin_auth` | Returns current Google Cloud Backup & Password Reset OTP settings (masked password), available local backups, and recent backup log entries. |
| `POST` | `/api/backup/config` | L2344-L2398 | `save_backup_config_endpoint()` | `require_super_admin_auth` | Saves unified Google Cloud Backup & Password Reset OTP SMTP credentials to .env, tests TLS login, and updates status. |
| `POST` | `/api/backup/run-now` | L2401-L2426 | `run_backup_now_endpoint()` | `require_super_admin_auth` | Triggers an immediate hot SQLite snapshot + .tar.gz archive + Google Cloud upload. |
| `GET` | `/api/backup/download-latest` | L2429-L2443 | `download_latest_backup_archive()` | `require_admin_auth` | Downloads the latest gpon_backup_*.tar.gz archive (or creates one on the fly if none exists). |
| `GET` | `/api/backup/list` | L2446-L2453 | `list_local_backups_endpoint()` | `require_admin_auth` | Lists all available local backup snapshots on the server. |
| `POST` | `/api/backup/restore-local` | L2456-L2488 | `restore_local_backup_endpoint()` | `require_super_admin_auth` | Restores survey database, users, and hierarchy from a selected local backup snapshot. |
| `POST` | `/api/backup/restore-upload` | L2491-L2525 | `restore_uploaded_backup_endpoint()` | `require_super_admin_auth` | Restores survey database, users, and hierarchy from an uploaded .tar.gz or .db backup file. |
| `GET` | `/api/download-hierarchy-template` | L2528-L2565 | `download_hierarchy_template()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/health` | L2572-L2578 | `health_check()` | `Public / Session` | REST Endpoint |
| `GET` | `/api/surveyed-points` | L2614-L2730 | `get_surveyed_points()` | `require_any_auth` | Returns an index map of all surveyed enclosure/splitter points across the network. |
| `POST` | `/api/sync` | L2733-L2906 | `sync_records()` | `require_any_auth` | REST Endpoint |
| `GET` | `/api/records` | L2910-L2973 | `get_all_records()` | `require_management_auth` | REST Endpoint |
| `PUT` | `/api/records/{client_uuid}` | L2976-L3041 | `update_survey_record()` | `require_admin_auth` | REST Endpoint |
| `DELETE` | `/api/records/{client_uuid}` | L3044-L3084 | `delete_record()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/bulk-delete` | L3087-L3110 | `bulk_delete_records()` | `require_any_auth` | REST Endpoint |
| `POST` | `/api/records/clear-center` | L3113-L3132 | `clear_center_records()` | `require_super_admin_auth` | REST Endpoint |
| `GET` | `/api/export-excel` | L3135-L3173 | `export_server_excel()` | `require_management_auth` | REST Endpoint |
| `GET` | `/api/export-center-excel` | L3176-L3261 | `export_center_excel()` | `require_export_auth` | Exports and downloads filtered survey Excel file streamed dynamically in-memory. |
| `GET` | `/api/export-data-zip` | L3264-L3336 | `export_data_zip()` | `require_management_auth` | Generates and downloads a ZIP archive of all region/center Excel files dynamically in-memory. |
| `GET` | `/api/reports/catalog` | L3350-L3352 | `api_reports_catalog()` | `require_export_auth` | Returns the catalog of available reports, their formats, categories, and descriptions. |
| `GET` | `/api/reports/download/{report_type}` | L3355-L3402 | `api_download_report()` | `require_export_auth` | Generates and downloads any of the enterprise reports: |
| `POST` | `/api/upload-survey-excel` | L3405-L3564 | `upload_survey_excel()` | `require_admin_auth` | Imports survey records from an Excel (.xlsx/.xls) or CSV file directly into SQLite in-memory. |
| `GET` | `/api/data-folders-summary` | L3567-L3649 | `get_data_folders_summary()` | `require_management_auth` | Returns structured summary of region and center survey records with dynamic in-memory exports. |
| `POST` | `/api/resync-data-folders` | L3652-L3660 | `resync_data_folders()` | `require_admin_auth` | Optimizes server storage and recounts SQLite records. |
| `GET` | `/docs/wiring` | L3665-L3670 | `get_wiring_report()` | `Public / Session` | Serves the interactive architecture wiring and blast-radius report. |
| `GET` | `/manifest.json` | L3673-L3685 | `get_pwa_manifest()` | `Public / Session` | REST Endpoint |
| `GET` | `/admin` | L3689-L8553 | `admin_dashboard()` | `Public / Session` | REST Endpoint |

## 3. SQLite Database Query Registry
| Line | Target Table | SQL Operation Snippet |
| :--- | :--- | :--- |
| L343 | `users` | `SELECT username, password, full_name, assigned_center, assigned_region, role, created_a...` |
| L417 | `unknown` | `PRAGMA journal_mode = WAL;` |
| L418 | `unknown` | `PRAGMA busy_timeout = 10000;` |
| L419 | `unknown` | `PRAGMA synchronous = NORMAL;` |
| L479 | `unknown` | `PRAGMA table_info(users)` |
| L482 | `users` | `ALTER TABLE users ADD COLUMN phone TEXT DEFAULT` |
| L484 | `users` | `ALTER TABLE users ADD COLUMN email TEXT DEFAULT` |
| L486 | `users` | `ALTER TABLE users ADD COLUMN assigned_region TEXT DEFAULT` |
| L488 | `users` | `ALTER TABLE users ADD COLUMN role TEXT DEFAULT` |
| L490 | `users` | `ALTER TABLE users ADD COLUMN created_at TEXT` |
| L491 | `users` | `UPDATE users SET phone =` |
| L492 | `users` | `UPDATE users SET email =` |
| L493 | `users` | `UPDATE users SET assigned_region =` |
| L494 | `users` | `UPDATE users SET role =` |
| L497 | `unknown` | `PRAGMA table_info(survey_records)` |
| L500 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_username TEXT` |
| L502 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN surveyor_name TEXT` |
| L504 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN splitter_lead_color TEXT` |
| L506 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN adl_subscriber_id TEXT` |
| L508 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN acs_subscriber_id TEXT` |
| L510 | `survey_records` | `ALTER TABLE survey_records ADD COLUMN survey_date_time TEXT` |
| L512 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center)` |
| L513 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id)` |
| L514 | `unknown` | `CREATE INDEX IF NOT EXISTS idx_records_enc_spl ON survey_records(enclosure_id, splitter...` |
| L551 | `users` | `SELECT username FROM users WHERE username =` |
| L560 | `users` | `UPDATE users SET role =` |
| L563 | `users` | `SELECT username, password FROM users` |
| L567 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L577 | `users` | `SELECT username, assigned_center FROM users` |
| L584 | `users` | `UPDATE users SET assigned_center = ? WHERE username = ?` |
| L694 | `users` | `SELECT role, assigned_region, assigned_center FROM users WHERE LOWER(TRIM(username)) = ...` |
| L1081 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1132 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1163 | `users` | `SELECT username, full_name, phone, email, assigned_center, assigned_region, role, creat...` |
| L1215 | `users` | `SELECT password, role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1435 | `users` | `SELECT password, assigned_center, assigned_region, phone, role FROM users WHERE LOWER(T...` |
| L1650 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1789 | `password_reset_otps` | `SELECT * FROM password_reset_otps WHERE username = ?` |
| L1797 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1803 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1810 | `password_reset_otps` | `UPDATE password_reset_otps SET attempts = ? WHERE username = ?` |
| L1817 | `users` | `UPDATE users SET password = ? WHERE LOWER(TRIM(username)) = ?` |
| L1818 | `password_reset_otps` | `DELETE FROM password_reset_otps WHERE username = ?` |
| L1851 | `users` | `SELECT * FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1872 | `users` | `UPDATE users SET password = ? WHERE username = ?` |
| L1884 | `users` | `SELECT role FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L1894 | `users` | `DELETE FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))` |
| L2893 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
| L2979 | `survey_records` | `SELECT client_uuid, olt_name, port_number, enclosure_number, enclosure_id, splitter_id ...` |
| L3056 | `survey_records` | `DELETE FROM survey_records WHERE client_uuid = ? OR LOWER(client_uuid) = LOWER(?)` |
| L3122 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?)) AND LOWER(TRIM(re...` |
| L3124 | `survey_records` | `DELETE FROM survey_records WHERE LOWER(TRIM(center)) = LOWER(TRIM(?))` |
| L3573 | `survey_records` | `SELECT region, center, COUNT(*) FROM survey_records GROUP BY region, center` |
| L3657 | `survey_records` | `SELECT COUNT(*) FROM survey_records` |
# Blast Radius & Change Impact Matrix
*Generated automatically on: 2026-10-10 17:20:35*

This document provides a comprehensive causal analysis answering:
**"What happens in the system when any line or component in the codebase changes?"**

## 1. Summary of System Failure Domains
A change in one file triggers ripple effects across 5 interrelated layers:
1. **PWA Client State (`localStorage`)**: Offline queue, cache versions, device IDs.
2. **Network Wire Contract**: JSON schemas, HTTP status codes, headers.
3. **Database Constraints**: SQLite table schemas, unique indexes, foreign keys.
4. **Administrative Visualizations**: Table aggregations, Leaflet map coordinates.
5. **Telecom Export Tooling**: Master Excel templates, SW Maps post-processors.

---

## 2. Comprehensive Impact & Blast-Radius Matrix

| Subsystem & Target Line | Specific Code Change | Failure Cascade & Blast Radius | Severity | Verification & Mitigation Protocol |
| :--- | :--- | :--- | :--- | :--- |
| **PWA Storage Key**<br>`app.js` L12 | Changing `STORAGE_KEY = 'gpon_survey_records_v1'` to a new name | **Total Field Data Isolation**: Technicians in the field lose access to all un-synced offline surveys stored in device `localStorage`. The app displays 0 records. Technicians assume data was deleted. | **CRITICAL** | **Never rename** without an automatic migration loop that checks for the old key and copies items to the new key on client startup. |
| **Enclosure ID Formula**<br>`app.js` L1090 & `server.py` L907 | Changing formula in `updateEnclosureId()` or `compute_server_enclosure_id()` | **Compound Code Inconsistency**: Client and server generate differing enclosure strings. Multi-port enclosures (`P1P2E15`) fail regex matching. Duplicate survey points are created. Master Excel exports cannot link splitters to enclosures. | **CRITICAL** | Keep client regex `/^[A-Z0-9]+P\d+(P\d+)*E\d+$/` in exact sync with server logic. Run automated enclosure calculation test suite. |
| **SQLite Schema**<br>`server.py` L379-528 (`init_db`) | Modifying or deleting column definitions without migration | **Server 500 Crash on Sync**: Active databases raise `sqlite3.OperationalError: table survey_records has no column...`. PWA sync requests fail with 500. Field apps retry indefinitely with exponential backoff. In-memory `openpyxl` exporter crashes. | **CRITICAL** | Always write migration scripts inside `init_db()` using `ALTER TABLE ... ADD COLUMN` wrapped in `try/except`. Never delete columns from SQLite without backup. |
| **RBAC Normalizer**<br>`server.py` L285 & `app.js` L403 | Renaming role strings (e.g. `field_technician` -> `technician`) | **Privilege Escalation / System Lockout**: Existing session tokens fail verification. Field technicians gain access to `/admin` dashboard or Admins get locked out with 403 Forbidden. Frontend buttons (`Master Excel`) disappear. | **CRITICAL** | Roles must strictly match the 4 canonical strings: `super_admin`, `rcsm`, `acso`, `field_technician`. Run RBAC test suite before deployment. |
| **Pydantic Model**<br>`server.py` L847 (`SurveyRecordModel`) | Adding new non-optional field to `SurveyRecordModel` | **422 Unprocessable Entity Errors**: Field survey devices running cached PWA service workers send payloads without the new field. FastAPI rejects all offline sync payloads. Technicians cannot upload data. | **HIGH** | All newly added fields in `SurveyRecordModel` must be `Optional[T] = None` with default fallback values. |
| **In-Memory Excel Exporter**<br>`server.py` L676 (`build_excel_workbook`) | Reordering columns or modifying header text | **Downstream Tooling Failure**: Telecom analytics scripts and `process_swmaps_export.py` parse columns by index. Reordering columns corrupts coordinates, customer counts, and enclosure mappings. | **HIGH** | Preserve standard 24-column template. Verify against `GPON OLT MAPPING TCR.xlsx` baseline. |
| **Service Worker Cache**<br>`web_app/sw.js` L1 | Modifying `CACHE_NAME` without cache invalidation | **Stale Asset Lockout**: Field devices continue serving old `app.js` and `index.html` indefinitely from browser cache. New bug fixes or features do not reflect in the field. | **MEDIUM** | Increment version (`v2` -> `v3`) and implement `caches.keys().then(...)` cleanup loop inside `activate` event. |
| **Jurisdiction Filter**<br>`server.py` L609 (`get_user_jurisdiction`) | Changing case-folding or center matching | **Empty Admin Dashboard**: RCSM and ACSO users see empty dashboards because their assigned center name does not match upper-cased hierarchy keys. | **MEDIUM** | Always normalize center names with `.strip().upper()` before lookup. |

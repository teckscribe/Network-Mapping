# Web App (app.js) Frontend Engine Wiring Dissection
*Generated automatically on: 2026-10-10 17:20:35*

Total lines in `web_app/app.js`: **4006** LOC

## 1. Subsystem Functional Blocks (Line-by-Line Breakdown)
The 3,960 lines of `web_app/app.js` are partitioned into 17 high-reliability client engines:

| Block | Line Range | Engine / Feature Name | Core Functions / Wiring Details |
| :--- | :--- | :--- | :--- |
| **Block 1** | L1 - L35 | **Global State & Configuration** | Coordinates, map markers, device UUID (`gpon_device_id`), server origin auto-discovery, and hierarchy version tracking. |
| **Block 2** | L36 - L130 | **Midnight Session Auto-Reset Engine** | Enforces daily security logouts at 00:00 local time. Automatically flushes pending records to server before terminating session. Functions: `scheduleMidnightLogout()`, `handleMidnightSessionReset()`, `clearSessionOnly()`. |
| **Block 3** | L131 - L375 | **Splitter Color Matrix & Lead Occupancy Cache** | Computes color-coded splitter capacities (1:8, 1:16, 1:32) and checks customer saturation. Functions: `getColorVariants()`, `getSplitterSummary()`, `getSurveyedInfo()`. |
| **Block 4** | L376 - L500 | **Surveyed Points Caching & Role Guard** | Fetches surveyed points for active center (`GET /api/surveyed-points`). Customizes UI controls and export buttons according to user role (`super_admin`, `rcsm`, `acso`, `field_technician`). Function: `updateUserBar()`. |
| **Block 5** | L501 - L755 | **Surveyor Authentication & Login Modal** | Manages offline and online authentication. Caches token in `localStorage['gpon_auth_token']`. Handles logout with unsynced records warning. Functions: `handleLogin()`, `logout()`. |
| **Block 6** | L756 - L1015 | **Password Reset Modal & OTP Verification Pipeline** | 3-step modal workflow: 1. Request OTP via email, 2. Enter 6-digit OTP code, 3. Set new password. Functions: `handleRequestResetOtp()`, `handleVerifyResetOtp()`. |
| **Block 7** | L1016 - L1100 | **Coordinate Parsing & Compound Enclosure ID Engine** | Parses manual/GPS coordinates. Automatically calculates compound Enclosure ID (e.g. `THN156OLT53P1E1` or multi-port `OPM1120LT25P1P2E15`). Function: `updateEnclosureId()`. |
| **Block 8** | L1101 - L1420 | **Dynamic Cascading Hierarchy Engine** | Dynamically populates dropdowns: `Region -> Center -> RT Room -> OLT -> Ports`. Eliminates hardcoded names. Functions: `initDropdowns()`, `onRegionChange()`, `updateCentersForSelectedRegion()`. |
| **Block 9** | L1421 - L1750 | **Splitter Selection & Lead Occupancy State Machine** | Dynamically renders available enclosures (`E1..E20`), splitters (`S1..S8`), and color leads (Blue, Orange, Green, Brown, Slate, White, Red, Black...). Enforces capacity limits. Functions: `updateAvailableEnclosures()`, `updateAvailableSplitters()`. |
| **Block 10** | L1751 - L2100 | **Auto-Population from Surveyed Enclosures** | When technician selects an existing enclosure on a pole, automatically auto-fills coordinates, KSEB post number, and landmark. Functions: `handleEnclosureChange()`, `onOLTChange()`. |
| **Block 11** | L2101 - L2300 | **Multi-Port Selection Modal & Checkbox Matrix** | Interactive modal allowing multi-selection of distribution feeder ports (e.g., `P1, P2`). Generates compound port strings. Functions: `renderPortCheckboxes()`, `updateSelectedPortsFromCheckboxes()`. |
| **Block 12** | L2301 - L2440 | **In-Browser Excel Hierarchy Parser** | Parses uploaded Node Master Excel workbooks directly in browser using `SheetJS` (`xlsx.full.min.js`). Function: `handleExcelHierarchyUpload()`. |
| **Block 13** | L2441 - L2715 | **High-Accuracy GPS Geolocation Engine & Map View** | Continuous `watchPosition` filtering for accuracy <15m. Renders interactive Leaflet map with pinpoint marker drag-and-drop. Functions: `captureGPS()`, `finalizeGPS()`, `initMap()`. |
| **Block 14** | L2716 - L3110 | **Form Validation & Record Persistence Engine** | Validates field entries, enforces customer count constraints, generates client UUID4, and saves records into `localStorage['gpon_survey_records_v1']`. Function: `saveRecord()`. |
| **Block 15** | L3111 - L3560 | **Client-Side Excel Exporter & Local Records Modal** | Generates formatted Excel (.xlsx) workbooks directly on mobile device using SheetJS. Provides table view of records with deletion support. Functions: `exportToExcel()`, `renderSheetTable()`, `executeDeleteRecord()`. |
| **Block 16** | L3561 - L3880 | **Offline-First Synchronization Engine** | Manages server heartbeats (`checkServerConnection`), sends un-synced batches to `POST /api/sync`, tracks sync status badges (`synced`, `pending`), and triggers automatic retries. Function: `syncWithServer()`. |
| **Block 17** | L3881 - L3960 | **PWA Application Bootstrap & Service Worker** | Initializes application state, checks midnight session expiration, registers `sw.js` for 100% offline functionality. Function: `bootApp()`. |

## 2. PWA Network Fetch Wire Registry

| Line | HTTP Method | Target Endpoint | Function Context | Payload / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| L388 | `GET` | `${serverUrl}/api/surveyed-points${query}` | REST Client | Network communication with central server |
| L589 | `POST` | `${serverUrl}/api/login` | REST Client | Network communication with central server |
| L744 | `POST` | `${serverUrl}/api/logout` | REST Client | Network communication with central server |
| L862 | `POST` | `${serverUrl}/api/request-password-reset-otp` | REST Client | Network communication with central server |
| L903 | `POST` | `${serverUrl}/api/request-password-reset-otp` | REST Client | Network communication with central server |
| L963 | `POST` | `${serverUrl}/api/verify-password-reset-otp` | REST Client | Network communication with central server |
| L3547 | `POST` | `${serverUrl}/api/records/bulk-delete` | REST Client | Network communication with central server |
| L3577 | `GET` | `${serverUrl}/api/health` | REST Client | Network communication with central server |
| L3607 | `GET` | `${serverUrl}/api/hierarchy` | REST Client | Network communication with central server |
| L3631 | `GET` | `${serverUrl}/api/user-profile` | REST Client | Network communication with central server |
| L3662 | `POST` | `${serverUrl}/api/upload-hierarchy` | REST Client | Network communication with central server |
| L3735 | `POST` | `${serverUrl}/api/records/bulk-delete` | REST Client | Network communication with central server |
| L3810 | `POST` | `${serverUrl}/api/sync` | REST Client | Network communication with central server |

## 3. LocalStorage State & Invariants
| Key | Scope & Type | Lifecycle | Description |
| :--- | :--- | :--- | :--- |
| `gpon_survey_records_v1` | Array of Records | **Permanent** | The core offline survey cache. NEVER cleared on logout or restart. |
| `gpon_device_id` | String UUID | **Permanent** | Unique client device identifier (`DEV-XXXXXX`). |
| `gpon_auth_token` | HMAC String | Session (24h) | Bearer authentication token for REST requests. |
| `gpon_logged_in_user` | JSON Object | Session (24h) | Current surveyor metadata, role, assigned center, and region. |
| `gpon_custom_hierarchy` | JSON Tree | Cache | Cached Node Master hierarchy tree for offline dropdown cascading. |
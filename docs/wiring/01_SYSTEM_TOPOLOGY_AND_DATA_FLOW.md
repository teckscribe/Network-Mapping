# System Topology & End-to-End Data Flow Specification
*Generated automatically on: 2026-10-10 17:20:35*

## 1. Executive Architectural Blueprint
The GPON Network Mapping platform is an enterprise-grade, offline-first field survey and GIS network documentation system. It connects field mobile Progressive Web Apps (PWA) with a 24/7 central Ubuntu/Windows server running FastAPI and SQLite.

```mermaid
flowchart TD
    subgraph MobilePWA ["Mobile Web Client (web_app/)"]
        GPS["High-Accuracy Geolocation Engine\n(watchPosition + bestAccuracy filter)"]
        Grid["Spreadsheet UI Grid\n(Outdoor Sunlight-Readable)"]
        CompoundCalc["Compound Enclosure ID Calculator\n(Multi-Port Regex + S1..Sn Badges)"]
        OfflineStore["localStorage Store\n(gpon_survey_records_v1)"]
        SyncClient["Offline Sync Engine\n(UUID4 Deduplication + Exponential Backoff)"]
    end

    subgraph CentralServer ["Central Server (server.py - 6,230 LOC)"]
        APIRouter["FastAPI REST Dispatcher\n(38 Active Endpoints)"]
        RBAC["4-Tier RBAC Gatekeeper\n(super_admin, rcsm, acso, field_technician)"]
        Jurisdiction["Jurisdiction Filter Engine\n(Dynamic Region-Center Trees)"]
        StreamingEngine["In-Memory Streaming Engine\n(openpyxl + zipfile in io.BytesIO)"]
        AdminDashboard["Single-Page Admin Dashboard\n(Embedded SPA with Leaflet Maps)"]
    end

    subgraph PersistenceLayer ["Persistence & Recovery Layer"]
        SQLiteDB[("SQLite Database: gpon_survey_data.db\n(WAL Mode, Zero Static File Bloat)")]
        UsersJSON["users_config.json\n(Dual-Sync Git Safe Guard)"]
        HierarchyJSON["custom_hierarchy.json\n(Dynamic 4-Tier Tree)"]
    end

    GPS --> Grid
    Grid --> CompoundCalc
    CompoundCalc --> OfflineStore
    OfflineStore --> SyncClient
    SyncClient -->|POST /api/sync (Idempotent JSON)| APIRouter
    APIRouter --> RBAC
    RBAC --> Jurisdiction
    Jurisdiction --> SQLiteDB
    APIRouter <--> UsersJSON
    APIRouter <--> HierarchyJSON
    SQLiteDB --> StreamingEngine
    StreamingEngine -->|Streamed .xlsx / .zip| AdminDashboard
```

## 2. Communication Protocols & Wire Payloads
1. **PWA to Server Sync (`POST /api/sync`)**:
   - **Payload**: Client UUID array (`client_uuid`, `enclosure_id`, `lat_long`, `splitter_id`, `splitter_ratio`, `customers_connected`, `surveyor_username`, etc.).
   - **Deduplication**: If `client_uuid` already exists in SQLite, it executes an update or skip depending on timestamps; otherwise executes an insert.
2. **In-Memory Streaming Architecture**:
   - All Excel downloads (`/api/export-excel`, `/api/export-center-excel`, `/api/export-data-zip`) generate `.xlsx` workbooks directly in RAM using `io.BytesIO` streams.
   - Zero static Excel files are created or stored on server disk, eliminating disk bloat, race conditions, and file lock deadlocks.
3. **Dual-Persistence Configuration**:
   - `users_config.json` mirrors the `users` SQLite table. Every user created, updated, or imported is written to both SQLite and JSON, guaranteeing survival across server migration and Git updates.

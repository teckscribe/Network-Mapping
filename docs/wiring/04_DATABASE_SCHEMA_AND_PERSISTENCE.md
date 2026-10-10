# Database Schema, Dual-Sync & Persistence Layer
*Generated automatically on: 2026-10-10 17:20:35*

## 1. SQLite Relational Schema (`gpon_survey_data.db`)

The system uses an asynchronous SQLite database optimized with WAL (Write-Ahead Logging) mode.

### Table `survey_records`
Stores every mapped fiber enclosure, optical splitter connection, utility pole, and subscriber link:
```sql
CREATE TABLE IF NOT EXISTS survey_records (
    client_uuid TEXT PRIMARY KEY,          -- Client-generated UUID4 (Idempotent PK)
    region TEXT,                          -- Geographical Region (e.g. Thrissur, Ernakulam)
    center TEXT,                          -- Operational Center (e.g. CHALAKKUDY)
    rt_room TEXT,                         -- Remote Terminal Room (e.g. Potta)
    technology TEXT,                      -- Network Tech (GPON / XG-PON)
    olt_name TEXT,                        -- OLT Node Identifier
    port_number TEXT,                     -- PON Feeder Port(s) (e.g. P1, P1P2)
    kseb_post_number TEXT,                -- Utility Pole Identifier (KSEB)
    landmark TEXT,                        -- Physical Landmark
    enclosure_number TEXT,                -- Enclosure Suffix (E1..E20)
    enclosure_id TEXT,                    -- Compound Enclosure ID (e.g. CKY116OLT01P1E1)
    lat_long TEXT,                        -- GPS Coordinates (Latitude, Longitude)
    splitter_id TEXT,                     -- Splitter Code (S1..S8)
    splitter_ratio TEXT,                  -- Splitter Optical Ratio (1:8, 1:16, 1:32)
    customers_connected INTEGER DEFAULT 0,-- Active Connected Customer Count
    device_id TEXT,                       -- Client Device UUID
    created_at TEXT,                      -- Timestamp of local survey capture
    synced_at TEXT,                       -- Timestamp of server ingestion
    surveyor_username TEXT,               -- Username of field surveyor
    surveyor_name TEXT,                   -- Full Name of field surveyor
    splitter_lead_color TEXT,             -- Color of fiber lead (Blue, Orange...)
    adl_subscriber_id TEXT,               -- ADL Subscriber ID
    acs_subscriber_id TEXT,               -- ACS Subscriber ID
    survey_date_time TEXT                 -- Survey ISO timestamp
);

-- Optimization Indexes
CREATE INDEX IF NOT EXISTS idx_records_center ON survey_records(center);
CREATE INDEX IF NOT EXISTS idx_records_region ON survey_records(region);
CREATE INDEX IF NOT EXISTS idx_records_enclosure ON survey_records(enclosure_id);
```

### Table `users`
Stores user authentication credentials, assigned geographical jurisdictions, and access roles:
```sql
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,            -- Unique login username
    password TEXT NOT NULL,               -- Salted HMAC-SHA256 password hash
    full_name TEXT NOT NULL,              -- Full display name
    assigned_center TEXT NOT NULL,        -- Center assignment (or 'ALL')
    assigned_region TEXT NOT NULL,        -- Region assignment (or 'ALL')
    role TEXT NOT NULL,                   -- Role: super_admin, rcsm, acso, field_technician
    created_at TEXT,                      -- User creation timestamp
    email TEXT,                           -- Email address for OTP password recovery
    phone TEXT                            -- Contact mobile number
);
```

### Table `password_reset_otps`
Manages secure password reset tokens:
```sql
CREATE TABLE IF NOT EXISTS password_reset_otps (
    username TEXT PRIMARY KEY,
    otp TEXT NOT NULL,                    -- 6-digit numeric OTP
    expires_at REAL NOT NULL,             -- UNIX epoch expiration (15-min window)
    attempts INTEGER DEFAULT 0,           -- Failed attempt counter (max 3)
    email TEXT,
    full_name TEXT
);
```

## 2. In-Memory Excel Export Streaming (`openpyxl`)
Export endpoints (`/api/export-excel`, `/api/export-center-excel`, `/api/export-data-zip`) construct spreadsheets directly in RAM:
- Buffer: `io.BytesIO()`
- Font: Arial 10pt with standardized header fills (`#0284c7`).
- Zero Disk Footprint: No static `.xlsx` or `.zip` files are written to the server's filesystem, preventing file locks and storage leaks.

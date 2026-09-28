# GPON / FTTH Network Mapping & Field Survey System

A complete, offline-first mobile survey and network mapping solution for telecom and optical fiber infrastructure (OLT nodes, KSEB electric poles, fiber enclosures, and splitters).

---

## 🌟 Key Features

1. **Standalone Mobile Web App (PWA)**:
   - 100% compatible with all Android devices (Chrome, Samsung Internet, Edge, Firefox).
   - "Install to Home Screen" support for a fullscreen native app experience.
   - Works **100% offline** in remote field areas with zero mobile internet.
   - High-accuracy GPS capture with interactive map pin-drop.
   - Real-time automatic **Enclosure ID** calculation (`THN156OLT53` + `P1` + `E1` ➔ `THN156OLT53P1E1`).
   - Local **Excel (.xlsx)** and **CSV** generation directly on the phone.

2. **Field Surveyor Login & Automatic Center Filtering**:
   - Field agents log in with their assigned username and PIN.
   - The app automatically **locks their assigned Center** and filters the OLT dropdown so technicians only see OLTs belonging to their own center.
   - Every survey entry records the surveyor's name and username.

3. **24/7 Ubuntu Desktop Server & Central Office Dashboard**:
   - Built on **FastAPI + SQLite** for robust, asynchronous performance.
   - Auto-restarting background `systemd` service (`gpon-server.service`).
   - Idempotent sync engine prevents duplicate records during network dropouts.
   - Central Office Dashboard (`/admin`) to view live survey feeds, manage field agents, and assign centers.
   - 1-click **Master Excel Export** consolidating all field teams' survey points into the official template format.

4. **SW Maps Survey Integration**:
   - Step-by-step layer templates and survey guidelines for Softwel SW Maps.
   - Automated Python & batch converter (`process_swmaps_export.py` / `Run_SW_Maps_Converter.bat`) to convert raw SW Maps CSV exports into the standard Excel format.

---

## 📁 Repository Structure

```
├── web_app/                          # Mobile Web App (PWA)
│   ├── index.html                    # Outdoor sunlight-readable UI
│   ├── style.css                     # High-contrast mobile touch styles
│   ├── app.js                        # PWA logic, GPS, sync engine, Excel exporter
│   ├── preload_data.js               # Network hierarchy & OLT definitions
│   ├── sw.js                         # Service Worker for 100% offline support
│   ├── manifest.json                 # PWA Android install configuration
│   └── vendor/                       # Bundled offline libraries (Leaflet, SheetJS)
├── server.py                         # FastAPI + SQLite central backend
├── serve_app.py                      # Local server with QR code for mobile devices
├── install_on_ubuntu.sh              # 1-line 24/7 systemd setup script for Ubuntu
├── requirements.txt                  # Python dependencies
├── Run_Mobile_App.bat                # Windows 1-click server launch
├── Run_SW_Maps_Converter.bat         # Windows 1-click SW Maps CSV converter
├── process_swmaps_export.py          # Python converter script
├── UBUNTU_SERVER_SETUP_GUIDE.md      # Detailed 24/7 server & remote sync guide
├── SW_MAPS_SURVEY_GUIDE.md           # SW Maps field survey manual
└── GPON OLT MAPPING TCR.xlsx         # Master data reference template
```

---

## 🚀 Quick Start Guide

### 1. Running Locally on Windows
Simply double-click:
```bash
Run_Mobile_App.bat
```
This starts the server on port `9001`, displays your local Wi-Fi IP and a QR code, and opens the application in your browser. Point your Android phone camera at the screen to open the app on your mobile device.

### 2. Deploying on 24/7 Ubuntu Desktop
Transfer this repository to your Ubuntu PC and run:
```bash
chmod +x install_on_ubuntu.sh
./install_on_ubuntu.sh
```
This automatically configures the Python virtual environment and enables the `gpon-server` systemd background service on port `9001`.

To view the Central Office Dashboard:
👉 Open `http://<ubuntu-ip>:9001/admin`

---

## 👥 Default Field User Accounts

| Username | Password / PIN | Full Name | Assigned Center | Dropdown Filter |
| :--- | :--- | :--- | :--- | :--- |
| `thrissur_agent` | `1234` | Thrissur Survey Agent | **Thrissur North** | Only OLTs for Thrissur North |
| `tmm_agent` | `1234` | Thathamangalam Agent | **Thathamangalm** | Only OLTs for Thathamangalam |
| `admin` | `admin123` | Central Administrator | **ALL** | Supervisor mode (All Centers) |

New field surveyor accounts and center assignments can be added anytime from the `/admin` portal.

---

## 📄 License
MIT License.

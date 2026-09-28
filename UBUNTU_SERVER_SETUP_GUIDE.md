# 24/7 Ubuntu Desktop Server Setup Guide for GPON Survey

This guide explains how to set up your **24/7 Ubuntu Desktop** as the central data collection server so field technicians can sync data from their Android phones anywhere in the field, with zero risk of data loss.

---

## 1. Zero Data Loss Architecture

```
[ Android Phones in Field ]
     │
     ├─ 1. Instant Local Save (Phone Storage / IndexedDB)
     │     - Safe even in 0 mobile signal or dead server
     │     - Technicians can download Excel / CSV on phone anytime
     │
     └─ 2. Auto-Sync when Internet is Detected (4G/5G or Wi-Fi)
           │
           ▼
[ Ubuntu Desktop (24/7 Server via AnyDesk) ]
     │
     ├─ FastAPI + SQLite Ledger (gpon_survey_data.db)
     │     - Idempotent upsert (no duplicates on network drops)
     │     - Systemd auto-restart on reboot/crash
     │
     ├─ Central Office Dashboard (http://<ubuntu-ip>:8000/admin)
     │     - Live count of surveyed poles across Kerala
     │     - Live table & feed
     │
     └─ Master Excel Export (http://<ubuntu-ip>:8000/api/export-excel)
           - 1-click download of consolidated master Excel file
```

---

## 2. Setting Up the Ubuntu Server (Via AnyDesk)

### Step 2.1: Transfer the Folder to Ubuntu
Copy the `GPON Network Mapping Data` folder to your Ubuntu desktop (e.g. into `/home/yourusername/gpon_server`).

### Step 2.2: Run the 1-Line Installer
Open a terminal in the folder on Ubuntu and run:
```bash
chmod +x install_on_ubuntu.sh
./install_on_ubuntu.sh
```

This automated script will:
1. Install Python3, Pip, and SQLite.
2. Install FastAPI, Uvicorn, Pandas, and OpenPyXL in a virtual environment.
3. Create a **systemd service** (`gpon-server.service`) that:
   - Starts automatically on Ubuntu boot.
   - Runs silently in the background 24/7.
   - Automatically restarts if there is any power interruption or crash.
4. Open port `8000` in the Ubuntu firewall.

---

## 3. How Field Technicians Connect from Anywhere (4G / 5G Mobile Data)

Technicians in the field will be using mobile SIM data (Airtel, Jio, Vi). To allow them to sync with your Ubuntu desktop from anywhere without paying for a static public IP:

### Recommended: Cloudflare Tunnel (100% Free & Secure)
Cloudflare Tunnel creates an encrypted tunnel from your Ubuntu desktop to the internet with free HTTPS (which also enables GPS in Chrome):

1. On your Ubuntu terminal, run:
   ```bash
   # Download cloudflared
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb

   # Start a free instant tunnel
   cloudflared tunnel --url http://localhost:8000
   ```
2. Cloudflare will give you an instant HTTPS link, for example:
   `https://random-words.trycloudflare.com`
3. In the Android app on technicians' phones:
   - Tap **"View (Records)"** → Tap **"⚙️ Server IP"** → Paste your Cloudflare URL.
4. Now, whenever technicians save a post anywhere in Kerala on 4G/5G, it syncs directly to your Ubuntu server in real time!

---

## 4. Useful Ubuntu Server Commands (Via AnyDesk)

- **Check Server Status:**
  ```bash
  sudo systemctl status gpon-server
  ```
- **View Live Incoming Sync Logs:**
  ```bash
  sudo journalctl -u gpon-server -f
  ```
- **Restart Server:**
  ```bash
  sudo systemctl restart gpon-server
  ```
- **Stop Server:**
  ```bash
  sudo systemctl stop gpon-server
  ```

---

## 5. Central Office Management & Master Excel Download

Whenever management or the GIS team needs the consolidated report:
1. Open any browser on the Ubuntu desktop or office network:
   - **Dashboard**: `http://localhost:8000/admin`
   - **Download Consolidated Excel**: `http://localhost:8000/api/export-excel`
2. The downloaded Excel file contains all poles surveyed by all field teams, formatted identically to `GPON OLT MAPPING TCR.xlsx`.

---

## 6. Field User Accounts & Automatic Center Filtering

Field technicians can log in with their assigned username and PIN. The app automatically locks and filters their center:

### Pre-Configured Default Accounts:
| Username | Password / PIN | Full Name | Assigned Center | What They See in Dropdown |
| :--- | :--- | :--- | :--- | :--- |
| **`thrissur_agent`** | `1234` | Thrissur Survey Agent | **Thrissur North** | Only OLTs for Thrissur North *(Mulamkunnathukavu)* |
| **`tmm_agent`** | `1234` | Thathamangalam Agent | **Thathamangalm** | Only OLTs for Thathamangalam *(Kollengode)* |
| **`admin`** | `admin123` | Central Administrator | **ALL** | All Centers & OLTs |

### How to Create New Surveyors via AnyDesk:
1. Open `http://localhost:8000/admin` in the browser on your Ubuntu desktop.
2. Click on the **"Field Users & Center Assignment"** tab.
3. Enter the surveyor's username, PIN, full name, and select their assigned **Center**.
4. Click **"➕ Add / Update User"**.
5. When that surveyor logs in on their Android phone, the Center is automatically locked to their assigned area, and only their center's OLTs will be visible!


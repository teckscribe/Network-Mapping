"""
GPON Field Survey Local Web Server (FastAPI + SQLite + StaticFiles)
Serves the web_app folder, API endpoints, detects local IP, and displays a QR code for Android phones.
"""

import socket
import webbrowser
import os
import sys
import uvicorn
from server import app, init_db

PORT = 9001

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'

def run():
    init_db()
    ip = get_local_ip()
    local_url = f"http://localhost:{PORT}"
    mobile_url = f"http://{ip}:{PORT}"
    admin_url = f"http://localhost:{PORT}/admin"

    print("=" * 68)
    print("        GPON FIELD SURVEY - FASTAPI & SQLITE SERVER")
    print("=" * 68)
    print(f" Android Mobile App : {mobile_url}")
    print(f" PC Browser App     : {local_url}")
    print(f" Admin Dashboard    : {admin_url}")
    print("-" * 68)
    print("Scan this QR code with your Android phone camera to open:")
    print()

    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(mobile_url)
        qr.print_ascii(invert=True)
    except Exception:
        pass

    print()
    print("=" * 68)
    print(" Administrator Login:")
    print("   Username: admin | Password: admin123 | Scope: Central Super Admin")
    print("   (Create surveyor and supervisor accounts from the /admin portal)")
    print("=" * 68)
    print(f"Server is running on port {PORT}. Press Ctrl+C to stop.")

    try:
        webbrowser.open(local_url)
    except Exception:
        pass

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

if __name__ == '__main__':
    run()

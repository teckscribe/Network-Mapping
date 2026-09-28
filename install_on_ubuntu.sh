#!/bin/bash
# ==============================================================================
# GPON Field Survey Server - Ubuntu 24/7 Setup & Auto-Start Service
# Run this script on your 24/7 Ubuntu Desktop (via AnyDesk or Terminal)
# ==============================================================================

set -e

echo "=== [1/4] Updating Package Lists & Installing Dependencies ==="
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv sqlite3 curl ufw

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

echo "=== [2/4] Setting up Python Virtual Environment in $APP_DIR ==="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== [3/4] Creating 24/7 Systemd Background Service ==="
SERVICE_FILE="/etc/systemd/system/gpon-server.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=GPON Network Mapping Survey Server (FastAPI + SQLite)
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "=== [4/4] Enabling and Starting gpon-server.service ==="
sudo systemctl daemon-reload
sudo systemctl enable gpon-server
sudo systemctl restart gpon-server

# Allow port 8000 through Ubuntu firewall
if sudo ufw status | grep -q "active"; then
    echo "Configuring UFW firewall to allow port 8000..."
    sudo ufw allow 8000/tcp
fi

# Detect Local IP Address
LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "=============================================================================="
echo "  SUCCESS! GPON Survey Server is now running 24/7 on this Ubuntu Desktop!   "
echo "=============================================================================="
echo " Local Network URL : http://$LOCAL_IP:8000"
echo " Central Dashboard : http://$LOCAL_IP:8000/admin"
echo " Download Excel    : http://$LOCAL_IP:8000/api/export-excel"
echo ""
echo " Useful Commands:"
echo "   - Check Status  : sudo systemctl status gpon-server"
echo "   - View Live Logs: sudo journalctl -u gpon-server -f"
echo "   - Restart Server: sudo systemctl restart gpon-server"
echo "=============================================================================="

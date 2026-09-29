#!/usr/bin/env bash
# ==============================================================================
# Configure 24/7 Power Reboot & Auto-Survival for GPON Mapping Platform
# Run with: sudo ./configure_247_reboot_survival.sh
# ==============================================================================

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run this script with sudo:"
    echo "  sudo ./configure_247_reboot_survival.sh"
    exit 1
fi

echo "=============================================================================="
echo " Configuring System Services for 24/7 Power Reboot Survival..."
echo "=============================================================================="

# 1. Enable gpon-server.service
if [ -f "/etc/systemd/system/gpon-server.service" ]; then
    echo "[1/5] Enabling gpon-server.service on boot..."
    systemctl daemon-reload
    systemctl enable gpon-server.service
    systemctl restart gpon-server.service
    echo "  -> gpon-server is ENABLED and ACTIVE"
else
    echo "[1/5] WARNING: /etc/systemd/system/gpon-server.service not found!"
fi

# 2. Enable tailscaled.service (Tailscale & Funnel)
if systemctl list-unit-files | grep -q "tailscaled.service"; then
    echo "[2/5] Enabling tailscaled.service on boot..."
    systemctl enable tailscaled.service
    systemctl restart tailscaled.service
    echo "  -> tailscaled is ENABLED and ACTIVE"
else
    echo "[2/5] WARNING: tailscaled service not found. Is Tailscale installed?"
fi

# 3. Enable cron.service (Google Drive 30-min Automated Backups)
echo "[3/5] Enabling cron.service on boot..."
systemctl enable cron.service
systemctl restart cron.service
echo "  -> cron is ENABLED and ACTIVE"

# 4. Enable cloudflared (if installed)
if systemctl list-unit-files | grep -q "cloudflared.service"; then
    echo "[4/5] Enabling cloudflared.service on boot..."
    systemctl enable cloudflared.service
    systemctl restart cloudflared.service
    echo "  -> cloudflared is ENABLED and ACTIVE"
else
    echo "[4/5] cloudflared.service not installed (Skipping, using Tailscale Funnel)"
fi

# 5. Disable Ubuntu Desktop Sleep & Suspend (CRITICAL FOR DESKTOPS)
echo "[5/5] Disabling Ubuntu Sleep/Suspend/Hibernate (Prevents Network Drop)..."
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
echo "  -> System sleep and suspend are permanently DISABLED"

echo ""
echo "=============================================================================="
echo " VERIFYING SERVICE STATUSES ACROSS REBOOT:"
echo "=============================================================================="
echo -n "  * gpon-server  : " && systemctl is-enabled gpon-server || true
echo -n "  * tailscaled   : " && systemctl is-enabled tailscaled || true
echo -n "  * cron         : " && systemctl is-enabled cron || true
if systemctl list-unit-files | grep -q "cloudflared.service"; then
    echo -n "  * cloudflared  : " && systemctl is-enabled cloudflared || true
fi
echo ""
echo "All software services will now survive reboots, crashes, and power cuts!"
echo "NOTE: For hardware auto-boot after electricity cut, ensure your PC BIOS"
echo "      has 'Restore on AC Power Loss' set to 'Power On'."
echo "=============================================================================="

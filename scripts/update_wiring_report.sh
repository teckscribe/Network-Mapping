#!/usr/bin/env bash
# ========================================================================
#  GPON Network Mapping - Automated Wiring Report Generator (Linux/Ubuntu)
# ========================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "[INFO] Scanning codebase and updating architecture wiring reports..."
python3 "$SCRIPT_DIR/generate_wiring_report.py"

echo "[SUCCESS] Wiring reports and interactive portal updated successfully!"
echo "[PATH] Markdown reports: docs/wiring/"
echo "[PATH] Interactive portal: web_app/wiring_report.html"

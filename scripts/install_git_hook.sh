#!/usr/bin/env bash
# ========================================================================
#  GPON Network Mapping - Install Git Hook (Linux/Ubuntu)
# ========================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_DEST="$SCRIPT_DIR/../.git/hooks/pre-commit"

if [ -f "$HOOK_DEST" ]; then
    chmod +x "$HOOK_DEST"
    echo "[SUCCESS] Pre-commit hook is active and marked executable (+x)."
else
    echo "[ERROR] Pre-commit hook not found at $HOOK_DEST"
    exit 1
fi

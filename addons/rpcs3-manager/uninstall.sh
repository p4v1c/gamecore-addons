#!/usr/bin/env bash
# rpcs3-manager — uninstall (removes everything install.sh created)
set -euo pipefail

ADDON_NAME="rpcs3-manager"
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

systemctl --user disable --now "${UNIT}" 2>/dev/null || true
rm -f "${UNIT_DIR}/${UNIT}"
systemctl --user daemon-reload
rm -rf "${ADDON_DIR}/.venv"
rm -f "${ADDON_DIR}/web/gamecore-nav.js" "${ADDON_DIR}/web/gamecore-nav.css"
echo "[${ADDON_NAME}] Uninstalled."

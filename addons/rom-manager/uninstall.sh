#!/usr/bin/env bash
# rom-manager — uninstall (removes everything install.sh created)
set -euo pipefail

ADDON_NAME="rom-manager"
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

systemctl --user disable --now "${UNIT}" 2>/dev/null || true
rm -f "${UNIT_DIR}/${UNIT}"
systemctl --user daemon-reload
rm -rf "${ADDON_DIR}/.venv"
echo "[${ADDON_NAME}] Uninstalled."

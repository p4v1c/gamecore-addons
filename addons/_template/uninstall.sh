#!/usr/bin/env bash
# Addon uninstall — remove everything install.sh created (except the repo
# checkout itself, which the manager keeps for other addons).
set -euo pipefail

ADDON_NAME="template"            # ← change me
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

systemctl --user disable --now "${UNIT}" 2>/dev/null || true
rm -f "${UNIT_DIR}/${UNIT}"
systemctl --user daemon-reload
rm -rf "${ADDON_DIR}/.venv"
echo "[${ADDON_NAME}] Uninstalled."

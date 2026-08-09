#!/usr/bin/env bash
# save-manager — install (idempotent; see docs/CREATING_AN_ADDON.md for the contract)
set -euo pipefail

ADDON_NAME="save-manager"
PORT=8772                        # keep in sync with addon.json
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

# gamecore-addon (api 1) hands us both roots and our own writable corner. The
# defaults keep `bash install.sh` working by hand on a pre-P3 box, where only
# GAMECORE_PATH is set — TEMPORARY, same lifetime as the fallback in catalog.py.
GAMECORE_DATA="${GAMECORE_DATA:-${GAMECORE_PATH}}"
ADDON_DATA_DIR="${ADDON_DATA_DIR:-${GAMECORE_DATA}/addons/${ADDON_NAME}}"
mkdir -p "${ADDON_DATA_DIR}"

echo "[${ADDON_NAME}] Python venv + dependencies"
python3 -m venv "${ADDON_DIR}/.venv"
if [[ "${OFFLINE:-0}" == "1" ]]; then
  "${ADDON_DIR}/.venv/bin/pip" install -q --no-index \
    --find-links "${PAYLOAD_DIR}/wheels" -r "${ADDON_DIR}/requirements.txt"
else
  "${ADDON_DIR}/.venv/bin/pip" install -q -r "${ADDON_DIR}/requirements.txt"
fi

echo "[${ADDON_NAME}] Shared components"
cp "${ADDON_DIR}/../../shared/py/sfo.py"          "${ADDON_DIR}/"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.js"  "${ADDON_DIR}/web/"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.css" "${ADDON_DIR}/web/"

echo "[${ADDON_NAME}] systemd user unit"
mkdir -p "${UNIT_DIR}"
cat > "${UNIT_DIR}/${UNIT}" <<EOF
[Unit]
Description=GameCore addon — Save Manager
After=network-online.target

[Service]
Type=simple
Environment=GAMECORE_PATH=${GAMECORE_PATH}
Environment=GAMECORE_DATA=${GAMECORE_DATA}
Environment=ADDON_DATA_DIR=${ADDON_DATA_DIR}
Environment=ADDON_PORT=${PORT}
Environment=ADDON_BASE=/saves
WorkingDirectory=${ADDON_DIR}
ExecStart=${ADDON_DIR}/.venv/bin/python server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "${UNIT}"
systemctl --user restart "${UNIT}"
echo "[${ADDON_NAME}] Installed — http://<box-ip>:${PORT}"

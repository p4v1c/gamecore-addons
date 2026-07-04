#!/usr/bin/env bash
# rom-manager — install (idempotent; see docs/CREATING_AN_ADDON.md for the contract)
set -euo pipefail

ADDON_NAME="rom-manager"
PORT=8770                        # keep in sync with addon.json
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

echo "[${ADDON_NAME}] Python venv + dependencies"
python3 -m venv "${ADDON_DIR}/.venv"
if [[ "${OFFLINE:-0}" == "1" ]]; then
  "${ADDON_DIR}/.venv/bin/pip" install -q --no-index \
    --find-links "${PAYLOAD_DIR}/wheels" -r "${ADDON_DIR}/requirements.txt"
else
  "${ADDON_DIR}/.venv/bin/pip" install -q -r "${ADDON_DIR}/requirements.txt"
fi

echo "[${ADDON_NAME}] Shared nav component"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.js"  "${ADDON_DIR}/web/"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.css" "${ADDON_DIR}/web/"

echo "[${ADDON_NAME}] systemd user unit"
mkdir -p "${UNIT_DIR}"
cat > "${UNIT_DIR}/${UNIT}" <<EOF
[Unit]
Description=GameCore addon — ROM Manager
After=network-online.target

[Service]
Type=simple
Environment=GAMECORE_PATH=${GAMECORE_PATH}
Environment=ADDON_PORT=${PORT}
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

#!/usr/bin/env bash
# Addon install script — MUST be idempotent (safe to re-run for updates).
#
# Contract — the gamecore-addon manager calls this with:
#   USER_NAME      user the addon runs as (the GameCore user)
#   GAMECORE_PATH  core install dir (default /opt/GameCore)
#   ADDON_DIR      this addon's directory inside the repo checkout (= runtime dir)
#   OFFLINE        "1" when installing from the GameCore OS ISO without network
#   PAYLOAD_DIR    offline assets dir (only meaningful when OFFLINE=1)
#
# Rules:
#   - own setup ONLY: venv/deps, systemd unit, config. Never touch the
#     registry (config/addons.json) — the manager owns it.
#   - when OFFLINE=1, do not hit the network; everything needed must be in
#     the repo or in PAYLOAD_DIR (list it in addon.json "offline_assets").
#   - service name convention: gamecore-addon-<name>.service
set -euo pipefail

ADDON_NAME="template"            # ← change me
PORT=8799                        # ← keep in sync with addon.json
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
Description=GameCore addon — ${ADDON_NAME}
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

#!/usr/bin/env bash
# Addon install script — MUST be idempotent (safe to re-run for updates).
#
# Contract v1 — the gamecore-addon manager calls this with:
#   USER_NAME            user the addon runs as (the GameCore user)
#   GAMECORE_PATH        core install dir (default /opt/GameCore) — READ ONLY
#   GAMECORE_DATA        player data dir (default $GAMECORE_PATH) — writable
#   ADDON_DATA_DIR       your writable corner, $GAMECORE_DATA/addons/<name>,
#                        already created when this script runs
#   GAMECORE_ADDON_API   the api version the manager speaks (1)
#   GAMECORE_BACKEND_PORT  the core backend's port (default 8765)
#   ADDON_DIR            this addon's directory inside the repo checkout (= runtime dir)
#   OFFLINE              "1" when installing from the GameCore OS ISO without network
#   PAYLOAD_DIR          offline assets dir (only meaningful when OFFLINE=1)
#
# Rules:
#   - WRITE TO $GAMECORE_DATA / $ADDON_DATA_DIR, NEVER TO $GAMECORE_PATH. The
#     install root is becoming a read-only mount; GAMECORE_PATH is handed to
#     you for reading. An addon that writes there installs fine today and fails
#     on the release that flips the mount, unattended.
#   - declare "api": 1 in addon.json once that is true. Without it the manager
#     refuses to install or update the addon, by name — deliberately, so a
#     pre-split addon cannot half-work instead.
#   - pass BOTH roots to your systemd unit, or your service will not see them.
#   - own setup ONLY: venv/deps, systemd unit, config. Never touch the
#     registry ($GAMECORE_DATA/config/addons.json) — the manager owns it.
#   - when OFFLINE=1, do not hit the network; everything needed must be in
#     the repo or in PAYLOAD_DIR (list it in addon.json "offline_assets").
#   - service name convention: gamecore-addon-<name>.service
set -euo pipefail

ADDON_NAME="template"            # ← change me
PORT=8799                        # ← keep in sync with addon.json
UNIT="gamecore-addon-${ADDON_NAME}.service"
UNIT_DIR="${HOME}/.config/systemd/user"

# Defaults keep `bash install.sh` working by hand on a pre-P3 box, where only
# GAMECORE_PATH is set — TEMPORARY, same lifetime as the fallback in server.py.
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
Environment=GAMECORE_DATA=${GAMECORE_DATA}
Environment=ADDON_DATA_DIR=${ADDON_DATA_DIR}
Environment=ADDON_PORT=${PORT}
Environment=ADDON_BASE=/template
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

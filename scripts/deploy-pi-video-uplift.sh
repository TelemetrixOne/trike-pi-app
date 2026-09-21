#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
START_SERVICES="${HPR_START_SERVICES:-true}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/opt/hpr/backups/video-uplift-${STAMP}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root, for example: sudo scripts/deploy-pi-video-uplift.sh" >&2
  exit 1
fi

for required in \
  bin/hpr-video-publish.sh \
  bin/hpr-video-compose.sh \
  hpr_gateway/config.py \
  hpr_gateway/services/trike_config_sync.py \
  scripts/validate-pi-gateway.sh
do
  if [ ! -f "${REPO_ROOT}/${required}" ]; then
    echo "Deployment source is missing: ${REPO_ROOT}/${required}" >&2
    exit 1
  fi
done

bash -n "${REPO_ROOT}/bin/hpr-video-publish.sh"
bash -n "${REPO_ROOT}/bin/hpr-video-compose.sh"
apt-get update
apt-get install -y --no-install-recommends \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-gl
install -d -m 0700 "${BACKUP_ROOT}"
cp -a "${CONFIG_PATH}" "${BACKUP_ROOT}/hpr.yaml"
for relative in \
  bin/hpr-video-publish.sh \
  bin/hpr-video-compose.sh \
  runtime/video/hpr-video-publish.sh \
  runtime/video/hpr-video-compose.sh \
  hpr_gateway/config.py \
  hpr_gateway/services/trike_config_sync.py \
  scripts/validate-pi-gateway.sh
do
  if [ -e "${INSTALL_ROOT}/${relative}" ]; then
    install -D -m 0600 "${INSTALL_ROOT}/${relative}" "${BACKUP_ROOT}/${relative}"
  fi
done
if [ -e /etc/systemd/system/hpr-video-compositor.service ]; then
  install -D -m 0600 /etc/systemd/system/hpr-video-compositor.service \
    "${BACKUP_ROOT}/etc/systemd/system/hpr-video-compositor.service"
fi

install -m 0755 "${REPO_ROOT}/bin/hpr-video-publish.sh" "${INSTALL_ROOT}/bin/hpr-video-publish.sh"
install -m 0755 "${REPO_ROOT}/bin/hpr-video-publish.sh" "${INSTALL_ROOT}/runtime/video/hpr-video-publish.sh"
install -m 0755 "${REPO_ROOT}/bin/hpr-video-compose.sh" "${INSTALL_ROOT}/bin/hpr-video-compose.sh"
install -m 0755 "${REPO_ROOT}/bin/hpr-video-compose.sh" "${INSTALL_ROOT}/runtime/video/hpr-video-compose.sh"
install -m 0644 "${REPO_ROOT}/hpr_gateway/config.py" "${INSTALL_ROOT}/hpr_gateway/config.py"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/trike_config_sync.py" \
  "${INSTALL_ROOT}/hpr_gateway/services/trike_config_sync.py"
install -m 0755 "${REPO_ROOT}/scripts/validate-pi-gateway.sh" "${INSTALL_ROOT}/scripts/validate-pi-gateway.sh"

cat > /etc/systemd/system/hpr-video-compositor.service <<EOF
[Unit]
Description=HPR direct dual-camera HDMI compositor and publishers
After=hpr-video-mediamtx.service hpr-video-console.service
Requires=hpr-video-mediamtx.service hpr-video-console.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=admin
Group=admin
SupplementaryGroups=video render
WorkingDirectory=${INSTALL_ROOT}/runtime/video
Environment=HPR_CONFIG_PATH=${CONFIG_PATH}
Environment=HPR_INSTALL_ROOT=${INSTALL_ROOT}
ExecStart=${INSTALL_ROOT}/runtime/video/hpr-video-compose.sh
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" \
  -m hpr_gateway.validate_config --config "${CONFIG_PATH}"

if [ "${START_SERVICES}" = "true" ]; then
  systemctl restart hpr-trike-config-sync.service
fi

echo "Video uplift installed. Backup: ${BACKUP_ROOT}"
echo "Configuration sync restart: ${START_SERVICES}"

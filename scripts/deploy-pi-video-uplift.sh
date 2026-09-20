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
  hpr_gateway/config.py \
  hpr_gateway/services/trike_config_sync.py
do
  if [ ! -f "${REPO_ROOT}/${required}" ]; then
    echo "Deployment source is missing: ${REPO_ROOT}/${required}" >&2
    exit 1
  fi
done

bash -n "${REPO_ROOT}/bin/hpr-video-publish.sh"
install -d -m 0700 "${BACKUP_ROOT}"
cp -a "${CONFIG_PATH}" "${BACKUP_ROOT}/hpr.yaml"
for relative in \
  bin/hpr-video-publish.sh \
  runtime/video/hpr-video-publish.sh \
  hpr_gateway/config.py \
  hpr_gateway/services/trike_config_sync.py
do
  if [ -e "${INSTALL_ROOT}/${relative}" ]; then
    install -D -m 0600 "${INSTALL_ROOT}/${relative}" "${BACKUP_ROOT}/${relative}"
  fi
done

install -m 0755 "${REPO_ROOT}/bin/hpr-video-publish.sh" "${INSTALL_ROOT}/bin/hpr-video-publish.sh"
install -m 0755 "${REPO_ROOT}/bin/hpr-video-publish.sh" "${INSTALL_ROOT}/runtime/video/hpr-video-publish.sh"
install -m 0644 "${REPO_ROOT}/hpr_gateway/config.py" "${INSTALL_ROOT}/hpr_gateway/config.py"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/trike_config_sync.py" \
  "${INSTALL_ROOT}/hpr_gateway/services/trike_config_sync.py"

PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" \
  -m hpr_gateway.validate_config --config "${CONFIG_PATH}"

if [ "${START_SERVICES}" = "true" ]; then
  systemctl restart hpr-trike-config-sync.service
fi

echo "Video uplift installed. Backup: ${BACKUP_ROOT}"
echo "Configuration sync restart: ${START_SERVICES}"

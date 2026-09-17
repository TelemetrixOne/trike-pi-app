#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRIKE_ID="${HPR_TRIKE_ID:-trike1}"
CONFIG_SOURCE="${HPR_CONFIG_SOURCE:-${REPO_ROOT}/hpr-standalone-docker/generated/pi-configs/${TRIKE_ID}/hpr.yaml}"
CONFIG_TARGET="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ROLLBACK_ROOT="/var/backups/hpr-pre-rebuild-${STAMP}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo env HPR_CONFIRM_REBUILD=YES scripts/rebuild-pi-gateway.sh" >&2
  exit 1
fi
if [ "${HPR_CONFIRM_REBUILD:-}" != "YES" ]; then
  echo "Refusing rebuild without HPR_CONFIRM_REBUILD=YES." >&2
  exit 1
fi
if [ ! -f "${CONFIG_SOURCE}" ]; then
  echo "Rendered Pi config not found: ${CONFIG_SOURCE}" >&2
  exit 1
fi

echo "Rebuilding ${TRIKE_ID} from ${CONFIG_SOURCE}"
echo "Rollback files: ${ROLLBACK_ROOT}"
install -d -m 0700 "${ROLLBACK_ROOT}/systemd"

if [ -d /etc/hpr ]; then
  cp -a /etc/hpr "${ROLLBACK_ROOT}/etc-hpr"
fi
find /etc/systemd/system -maxdepth 1 -type f \
  \( -name 'hpr-*.service' -o -name 'mediamtx.service' \) \
  -exec cp -a -t "${ROLLBACK_ROOT}/systemd" {} + 2>/dev/null || true

units=(
  hpr-heartbeat.service
  hpr-pi-power-health.service
  hpr-services-health.service
  hpr-trike1-services-health.service
  hpr-tpms.service
  hpr-tpms-all.service
  hpr-gps.service
  hpr-heart-rate.service
  hpr-hrmpro.service
  hpr-power-cadence.service
  hpr-pedals.service
  hpr-gpio-control.service
  hpr-derailleur.service
  hpr-power-watch.service
  hpr-video-mediamtx.service
  hpr-video-front.service
  hpr-video-rear.service
  mediamtx.service
)
for unit in "${units[@]}"; do
  systemctl disable --now "${unit}" >/dev/null 2>&1 || true
done

if [ -d /opt/hpr ]; then
  mv /opt/hpr "${ROLLBACK_ROOT}/opt-hpr"
fi

for legacy_unit in \
  hpr-tpms-all.service hpr-hrmpro.service hpr-pedals.service \
  hpr-trike1-services-health.service mediamtx.service
do
  rm -f "/etc/systemd/system/${legacy_unit}"
done
systemctl daemon-reload

install -d -m 0750 "$(dirname "${CONFIG_TARGET}")"
install -m 0640 -o root -g "$(stat -c '%G' "${CONFIG_SOURCE}")" \
  "${CONFIG_SOURCE}" "${CONFIG_TARGET}"

HPR_CONFIG_PATH="${CONFIG_TARGET}" HPR_INSTALL_SCOPE=all \
  "${REPO_ROOT}/scripts/install-pi-gateway.sh"

HPR_CONFIG_PATH="${CONFIG_TARGET}" \
  "${REPO_ROOT}/scripts/validate-pi-gateway.sh"

echo "Rebuild completed without rebooting the Pi."
echo "Retain ${ROLLBACK_ROOT} until post-race validation is complete."

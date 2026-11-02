#!/usr/bin/env bash
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_SOURCE="${HPR_CONFIG_SOURCE:-}"
CONFIG_TARGET="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ROLLBACK_ROOT="/var/backups/hpr-pre-rebuild-${STAMP}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo env HPR_CONFIRM_REBUILD=YES scripts/rebuild-pi-gateway.sh" >&2
  exit 1
fi
if [ "${HPR_CONFIRM_REBUILD:-}" != "YES" ] || [ "${HPR_STATIONARY_APPROVED:-}" != "YES" ]; then
  echo "Refusing rebuild without HPR_CONFIRM_REBUILD=YES and HPR_STATIONARY_APPROVED=YES." >&2
  exit 1
fi
if [ ! -f "${CONFIG_SOURCE}" ]; then
  echo "Rendered Pi config not found: ${CONFIG_SOURCE}" >&2
  exit 1
fi
case "$(realpath "${REPO_ROOT}")" in
  /opt/hpr|/opt/hpr/*) echo 'Run rebuild from a checkout outside /opt/hpr.' >&2; exit 1 ;;
esac
for pending in .trike-config-sync-pending .gpio-sync-pending.json .tpms-sync-pending.json .power-cadence-sync-pending.json; do
  if [ -e "$(dirname "${CONFIG_TARGET}")/${pending}" ]; then
    echo 'Configuration recovery is pending; preserve and resolve it before rebuilding.' >&2; exit 1
  fi
done

echo "Rebuilding from explicitly enrolled configuration ${CONFIG_SOURCE}"
echo "Rollback files: ${ROLLBACK_ROOT}"
install -d -m 0700 "${ROLLBACK_ROOT}/systemd"
cp -a "${CONFIG_SOURCE}" "${ROLLBACK_ROOT}/enrolled-config.yaml"
CONFIG_SOURCE="${ROLLBACK_ROOT}/enrolled-config.yaml"

if [ -d /etc/hpr ]; then
  cp -a /etc/hpr "${ROLLBACK_ROOT}/etc-hpr"
fi
find /etc/systemd/system -maxdepth 1 -type f \
  \( -name 'hpr-*.service' -o -name 'mediamtx.service' \) \
  -exec cp -a -t "${ROLLBACK_ROOT}/systemd" {} + 2>/dev/null || true
find /etc/systemd/system -maxdepth 1 -type d -name 'hpr-*.service.d' \
  -exec cp -a -t "${ROLLBACK_ROOT}/systemd" {} +

units=(
  hpr-heartbeat.service
  hpr-pi-power-health.service
  hpr-services-health.service
  hpr-health-agent.service
  hpr-trike1-health-agent.service
  hpr-demo-show.service
  hpr-trike-config-sync.service
  hpr-gpio-config-sync.service
  hpr-tpms-config-sync.service
  hpr-power-cadence-config-sync.service
  hpr-bluetooth-recovery.service
  hpr-gps-recovery.service
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
  hpr-video-compositor.service
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

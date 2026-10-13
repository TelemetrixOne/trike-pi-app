#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
BACKUP="$(realpath "${1:?Supply the system-metrics backup directory}")"
[[ "$BACKUP" == /opt/hpr/backups/system-metrics-* && -f "$BACKUP/services_health.py" ]] || {
  echo 'Not a valid system-metrics backup' >&2; exit 1;
}
cp -a "$BACKUP/services_health.py" /opt/hpr/gateway/hpr_gateway/services/services_health.py
if [ -e /opt/hpr/gateway/hpr_gateway/runtime_health.py ]; then
  cp -a /opt/hpr/gateway/hpr_gateway/runtime_health.py "$BACKUP/runtime-health-before-rollback-$(date -u +%Y%m%dT%H%M%SZ).py"
fi
if [ -f "$BACKUP/runtime_health.py" ]; then
  cp -a "$BACKUP/runtime_health.py" /opt/hpr/gateway/hpr_gateway/runtime_health.py
elif [ -e /opt/hpr/gateway/hpr_gateway/runtime_health.py ]; then
  mv /opt/hpr/gateway/hpr_gateway/runtime_health.py "$BACKUP/removed-runtime-health-$(date -u +%Y%m%dT%H%M%SZ).py"
fi
systemctl restart hpr-services-health.service
systemctl is-active --quiet hpr-services-health.service
echo 'Prior service-health publisher restored. Retained metrics age out via observed_at; no config changes.'

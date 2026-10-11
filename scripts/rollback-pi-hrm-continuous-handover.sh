#!/usr/bin/env bash
set -euo pipefail
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && "$BACKUP" == /opt/hpr/backups/hrm-handover-* &&
   -f "$BACKUP/heart_rate.py" && -f "$BACKUP/bluetooth_recovery.py" ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-hrm-continuous-handover.sh' >&2
  exit 1
}
cp -a "$BACKUP/heart_rate.py" "${INSTALL_ROOT}/hpr_gateway/services/heart_rate.py"
cp -a "$BACKUP/bluetooth_recovery.py" "${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
systemctl restart hpr-bluetooth-recovery.service
systemctl restart hpr-heart-rate.service
echo 'Previous HRM service and recovery module restored.'

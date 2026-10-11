#!/usr/bin/env bash
set -euo pipefail
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && "$BACKUP" == /opt/hpr/backups/bluetooth-recovery-* && -f "$BACKUP/validate-pi-gateway.sh" ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-bluetooth-recovery.sh' >&2
  exit 1
}
systemctl disable --now hpr-bluetooth-recovery.service
rm -f -- /etc/systemd/system/hpr-bluetooth-recovery.service "${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
cp -a "$BACKUP/validate-pi-gateway.sh" "${INSTALL_ROOT}/scripts/validate-pi-gateway.sh"
systemctl daemon-reload
echo 'HRM scanner recovery removed; no other services restarted.'

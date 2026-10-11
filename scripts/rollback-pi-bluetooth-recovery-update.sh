#!/usr/bin/env bash
set -euo pipefail
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && "$BACKUP" == /opt/hpr/backups/bluetooth-recovery-update-* &&
   -f "$BACKUP/bluetooth_recovery.py" && -f "$BACKUP/bluetooth.py" ]] || {
  echo 'Pass the exact backup directory printed by update-pi-bluetooth-recovery.sh' >&2
  exit 1
}
cp -a "$BACKUP/bluetooth_recovery.py" "${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
cp -a "$BACKUP/bluetooth.py" "${INSTALL_ROOT}/hpr_gateway/bluetooth.py"
systemctl restart hpr-bluetooth-recovery.service
echo 'Scanner watchdog and controller resolver restored; only watchdog restarted.'

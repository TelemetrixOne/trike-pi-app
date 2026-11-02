#!/usr/bin/env bash
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && "$BACKUP" == /opt/hpr/backups/bluetooth-scan-health-* &&
   -f "$BACKUP/bluetooth_recovery.py" ]] || {
  echo 'Pass the exact backup directory printed by update-pi-bluetooth-scan-health.sh' >&2
  exit 1
}
cp -a "$BACKUP/bluetooth_recovery.py" "${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
systemctl restart hpr-bluetooth-recovery.service
echo 'Previous scanner watchdog restored; only watchdog restarted.'

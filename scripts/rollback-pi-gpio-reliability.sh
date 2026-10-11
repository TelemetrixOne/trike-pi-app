#!/usr/bin/env bash
set -euo pipefail
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
BACKUP="${1:-}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ "$BACKUP" == /opt/hpr/backups/gpio-reliability-* &&
   -f "$BACKUP/gpio_control.py" && -f "$BACKUP/gpio_profile.py" ]] || {
    echo 'Pass the exact backup path printed by deploy-pi-gpio-reliability.sh' >&2; exit 1;
}
cp -a "$BACKUP/gpio_control.py" "${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py"
cp -a "$BACKUP/gpio_profile.py" "${INSTALL_ROOT}/hpr_gateway/gpio_profile.py"
systemctl restart hpr-gpio-control.service
systemctl try-restart hpr-gpio-config-sync.service hpr-trike-config-sync.service
echo 'Previous GPIO code restored; current central configuration preserved.'

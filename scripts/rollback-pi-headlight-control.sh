#!/usr/bin/env bash
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
TARGET="${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && "$BACKUP" == /opt/hpr/backups/headlight-control-* && -f "$BACKUP/gpio_control.py" ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-headlight-control.sh' >&2
  exit 1
}
cp -a "$BACKUP/gpio_control.py" "$TARGET"
systemctl restart hpr-gpio-control.service
systemctl is-active --quiet hpr-gpio-control.service
echo 'Previous GPIO controller restored; only hpr-gpio-control.service restarted.'

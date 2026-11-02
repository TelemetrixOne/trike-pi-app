#!/usr/bin/env bash
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
BACKUP="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
TARGET="${INSTALL_ROOT}/bin/hpr-demo-sequence.py"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP" && -d "$BACKUP" && "$BACKUP" == /opt/hpr/backups/demo-trigger-* ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-demo-trigger.sh' >&2
  exit 1
}
if [[ -f "$BACKUP/hpr-demo-sequence.py" ]]; then
  cp -a "$BACKUP/hpr-demo-sequence.py" "$TARGET"
else
  rm -f -- "$TARGET"
fi
echo 'Previous local demo command restored. No services restarted or demo triggered.'

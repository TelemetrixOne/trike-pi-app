#!/usr/bin/env bash
# Remove only the startup-show installation and restore any saved predecessors.
set -euo pipefail
BACKUP_ROOT="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP_ROOT" && -d "$BACKUP_ROOT" && "$BACKUP_ROOT" == /opt/hpr/backups/startup-show-* ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-startup-show.sh' >&2
  exit 1
}
systemctl disable --now hpr-startup-show.service 2>/dev/null || true
for target in \
  "${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh" \
  "${INSTALL_ROOT}/runtime/video/startup-splash.png" \
  "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py" \
  /etc/systemd/system/hpr-video-splash.service \
  /etc/systemd/system/hpr-startup-show.service \
  /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf; do
  if [[ -f "${BACKUP_ROOT}${target}" ]]; then
    cp -a "${BACKUP_ROOT}${target}" "$target"
  else
    rm -f -- "$target"
  fi
done
systemctl daemon-reload
echo 'Startup show rolled back; no video or GPIO service was restarted.'

#!/usr/bin/env bash
# Remove only the demo-show installation and restore any saved predecessors.
set -euo pipefail
BACKUP_ROOT="${1:-}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -n "$BACKUP_ROOT" && -d "$BACKUP_ROOT" && "$BACKUP_ROOT" == /opt/hpr/backups/demo-show-* ]] || {
  echo 'Pass the exact backup directory printed by deploy-pi-demo-show.sh' >&2
  exit 1
}
systemctl disable --now hpr-demo-show.service 2>/dev/null || true
for target in \
  "${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh" \
  "${INSTALL_ROOT}/runtime/video/hpr-video-native.py" \
  "${INSTALL_ROOT}/runtime/video/startup-splash.png" \
  "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py" \
  "${INSTALL_ROOT}/hpr_gateway/services/demo_show.py" \
  /etc/systemd/system/hpr-video-splash.service \
  /etc/systemd/system/hpr-startup-show.service \
  /etc/systemd/system/hpr-demo-show.service \
  /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf; do
  if [[ -f "${BACKUP_ROOT}${target}" ]]; then
    cp -a "${BACKUP_ROOT}${target}" "$target"
  else
    rm -f -- "$target"
  fi
done
systemctl daemon-reload
if [[ -f "${BACKUP_ROOT}/.native-updated" ]]; then
  systemctl restart hpr-video-compositor.service
fi
echo 'Demo show rolled back; video compositor restarted only if its native code changed. GPIO was not restarted.'

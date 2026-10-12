#!/usr/bin/env bash
# Restore the targeted deployment without altering central configuration.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
BACKUP="$(realpath "${1:?Supply the deployment backup directory}")"
[[ "$BACKUP" == /opt/hpr/backups/camera-controls-* && -f "$BACKUP/native-bin.py" && -f "$BACKUP/native-runtime.py" ]] || {
  echo 'Not a valid camera-controls backup' >&2; exit 1;
}
if [ -e /etc/hpr/camera-controls.yaml ]; then
  cp -a /etc/hpr/camera-controls.yaml "$BACKUP/controls-before-rollback-$(date -u +%Y%m%dT%H%M%SZ).yaml"
fi
cp -a "$BACKUP/native-bin.py" /opt/hpr/gateway/bin/hpr-video-native.py
cp -a "$BACKUP/native-runtime.py" /opt/hpr/gateway/runtime/video/hpr-video-native.py
if [ -f "$BACKUP/camera-controls.yaml" ]; then
  cp -a "$BACKUP/camera-controls.yaml" /etc/hpr/camera-controls.yaml
elif [ -f "$BACKUP/controls-was-absent" ] && [ -e /etc/hpr/camera-controls.yaml ]; then
  mv /etc/hpr/camera-controls.yaml "$BACKUP/removed-local-controls-$(date -u +%Y%m%dT%H%M%SZ).yaml"
fi
systemctl restart hpr-video-compositor.service
systemctl is-active --quiet hpr-video-compositor.service
echo 'Prior video software restored; local tuning preserved in backup. Hardware picture values are not reset by rollback.'

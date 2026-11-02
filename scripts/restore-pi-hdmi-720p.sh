#!/usr/bin/env bash
# Restore the shared 720p source while retaining the 800x600 stream canvas.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
for TARGET in "$ROOT/bin/hpr-video-native.py" "$ROOT/runtime/video/hpr-video-native.py"; do
  echo "c6b2f2e9f9cc3e8057a1d1a56636eb3bf245bf5fe3ddc2115fc341e01cee1848  $TARGET" | sha256sum -c -
done
BACKUP="/opt/hpr/backups/restore-hdmi-720p-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/bin/hpr-video-native.py" "$BACKUP/native-bin.py"
cp -a "$ROOT/runtime/video/hpr-video-native.py" "$BACKUP/native-runtime.py"
cp -a /etc/hpr/video-local.yaml "$BACKUP/video-local.yaml"
restore() {
  cp -a /etc/hpr/video-local.yaml "$BACKUP/failed-video-local.yaml"
  cp -a "$BACKUP/native-bin.py" "$ROOT/bin/hpr-video-native.py"
  cp -a "$BACKUP/native-runtime.py" "$ROOT/runtime/video/hpr-video-native.py"
  cp -a "$BACKUP/video-local.yaml" /etc/hpr/video-local.yaml
  systemctl restart hpr-video-compositor.service
  echo "Restored previous state from $BACKUP" >&2
}
trap restore ERR
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$ROOT/bin/hpr-video-native.py"
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$ROOT/runtime/video/hpr-video-native.py"
bash "$REPO_ROOT/scripts/apply-pi-local-video.sh" "$REPO_ROOT/config/video-local.front-800x600.yaml"
trap - ERR
echo "HDMI 720p source restored; backup: $BACKUP"

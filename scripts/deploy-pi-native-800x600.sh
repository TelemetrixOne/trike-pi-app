#!/usr/bin/env bash
# Guarded native-mode support update, followed by the normal profile applicator.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
echo '93460c68d1519432e5dbb5915d4950370f994daabcb1e1953708d451e14ba230  /opt/hpr/gateway/hpr_gateway/video_local.py' | sha256sum -c -
for TARGET in "$ROOT/bin/hpr-video-native.py" "$ROOT/runtime/video/hpr-video-native.py"; do
  echo "7b5afebda9327bf3beaf08bfcd1b08e8ce97979c377b6b9b6728a6be4356c8fb  $TARGET" | sha256sum -c -
done
BACKUP="/opt/hpr/backups/native-800x600-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/hpr_gateway/video_local.py" "$BACKUP/video_local.py"
cp -a "$ROOT/bin/hpr-video-native.py" "$BACKUP/native-bin.py"
cp -a "$ROOT/runtime/video/hpr-video-native.py" "$BACKUP/native-runtime.py"
cp -a /etc/hpr/video-local.yaml "$BACKUP/video-local.yaml"
restore() {
  cp -a /etc/hpr/video-local.yaml "$BACKUP/failed-video-local.yaml"
  cp -a "$BACKUP/video_local.py" "$ROOT/hpr_gateway/video_local.py"
  cp -a "$BACKUP/native-bin.py" "$ROOT/bin/hpr-video-native.py"
  cp -a "$BACKUP/native-runtime.py" "$ROOT/runtime/video/hpr-video-native.py"
  cp -a "$BACKUP/video-local.yaml" /etc/hpr/video-local.yaml
  systemctl restart hpr-video-compositor.service
  echo "Restored previous source/config from $BACKUP" >&2
}
trap restore ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/video_local.py" "$ROOT/hpr_gateway/video_local.py"
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$ROOT/bin/hpr-video-native.py"
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$ROOT/runtime/video/hpr-video-native.py"
bash "$REPO_ROOT/scripts/apply-pi-local-video.sh" "$REPO_ROOT/config/video-local.front-800x600.yaml"
trap - ERR
echo "Native 800x600 deployed; backup: $BACKUP"

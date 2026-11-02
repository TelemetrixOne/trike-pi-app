#!/usr/bin/env bash
# Install source with timestamp explicitly OFF; benchmark before enabling.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
DROPIN=/etc/systemd/system/hpr-video-compositor.service.d/80-video-timestamp.conf
[[ ! -e "$DROPIN" && ! -e "$ROOT/hpr_gateway/video_timestamp.py" ]] || { echo 'Unidentified existing timestamp configuration/source; stop' >&2; exit 1; }
export PYTHONPATH="$REPO_ROOT"
"$ROOT/venv/bin/python" - "$ROOT" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
hashes = {
 'hpr-video-native.py': '685fb53a05af1bcc7829c14c208409e0062106653fe85682bf0745df590d2678',
 'hpr-video-compose-gst.sh': '96f2db97f4a2ef5092ed4bcad2c639af738d2fd18295ed9479ab7501df226d21',
 'hpr-video-compose.sh': '640f561b39ce1a60a32fd7fe3f74435f18c6d534a25aa206f2516887e60f0ab1',
 'hpr-video-publish.sh': 'fc5823862ef9a09574c5b2414d54ceab4b2b0ae4ff33746af09fc6332be6815b',
}
for name, expected in hashes.items():
    for directory in ('bin', 'runtime/video'):
        path = root / directory / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, f'Unidentified live enhancement: {path}'
PY
systemctl is-active --quiet hpr-video-compositor.service
BACKUP="/opt/hpr/backups/video-timestamp-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP/bin" "$BACKUP/runtime"
for NAME in hpr-video-native.py hpr-video-compose-gst.sh hpr-video-compose.sh hpr-video-publish.sh; do
  cp -a "$ROOT/bin/$NAME" "$BACKUP/bin/$NAME"
  cp -a "$ROOT/runtime/video/$NAME" "$BACKUP/runtime/$NAME"
done
cp -a /etc/hpr/hpr.yaml /etc/hpr/video-local.yaml /etc/hpr/camera-controls.yaml "$BACKUP/"
systemctl cat hpr-video-compositor.service > "$BACKUP/unit.txt"
# The Pi image can have core GStreamer without the pango/clockoverlay plugin.
# This adds one scoped package, never upgrades the system or installs recommends.
if ! gst-inspect-1.0 clockoverlay >/dev/null 2>&1; then
  apt-get install -y --no-upgrade --no-install-recommends gstreamer1.0-x
fi
gst-inspect-1.0 clockoverlay >/dev/null
restore() {
  trap - ERR
  for NAME in hpr-video-native.py hpr-video-compose-gst.sh hpr-video-compose.sh hpr-video-publish.sh; do
    cp -a "$BACKUP/bin/$NAME" "$ROOT/bin/$NAME"
    cp -a "$BACKUP/runtime/$NAME" "$ROOT/runtime/video/$NAME"
  done
  # Keep the newly introduced helper and OFF switch; no new overlay is active.
  systemctl daemon-reload
  systemctl restart hpr-video-compositor.service
  echo "Source restored from $BACKUP; timestamp OFF" >&2
}
trap restore ERR
install -d "$(dirname "$DROPIN")"
printf '[Service]\nEnvironment=HPR_VIDEO_TIMESTAMP_ENABLED=0\n' > "$DROPIN"
install -m 0644 "$REPO_ROOT/hpr_gateway/video_timestamp.py" "$ROOT/hpr_gateway/video_timestamp.py"
for NAME in hpr-video-native.py hpr-video-compose-gst.sh hpr-video-compose.sh hpr-video-publish.sh; do
  install -m 0755 "$REPO_ROOT/bin/$NAME" "$ROOT/bin/$NAME"
  install -m 0755 "$REPO_ROOT/bin/$NAME" "$ROOT/runtime/video/$NAME"
done
systemctl daemon-reload
systemctl restart hpr-video-compositor.service
PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-local-video.py"
for NAME in hpr.yaml video-local.yaml camera-controls.yaml; do cmp "/etc/hpr/$NAME" "$BACKUP/$NAME"; done
trap - ERR
echo "Source installed with overlay OFF. Backup: $BACKUP. Run baseline/trial acceptance next."

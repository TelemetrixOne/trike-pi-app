#!/usr/bin/env bash
# Apply only the local picture profile, preserving the original and all services.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
PROFILE="$(realpath "${1:?Supply a camera-controls YAML profile}")"
CONFIG=/etc/hpr/camera-controls.yaml
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP="/opt/hpr/backups/camera-picture-$(date -u +%Y%m%dT%H%M%SZ)"
[[ -f "$CONFIG" && -f "$PROFILE" ]] || { echo 'Missing existing or candidate profile' >&2; exit 1; }
# Validate syntax and every connected camera's range before changing anything.
/usr/bin/python3 - "$PROFILE" <<'PY'
import importlib.util, json, time, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('video', '/opt/hpr/gateway/runtime/video/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)
profile = video.load_picture_controls(sys.argv[1])
status = json.loads(Path('/run/hpr-video/video-status.json').read_text())
if time.monotonic() - status['at'] > 5:
    raise SystemExit('Video status is stale')
manager = video.CameraControls()
for role, values in profile.items():
    if not values:
        continue
    camera = status['camera_controls']['cameras'].get(role, {})
    if camera.get('state') != 'applied':
        raise SystemExit(f'{role}: cannot verify connected camera')
    caps = video.parse_picture_capabilities(manager.command(camera['device'], '--list-ctrls-menus'))
    for name, value in values.items():
        c = caps.get(name, {})
        if not c or not c.get('min', 0) <= value <= c.get('max', -1):
            raise SystemExit(f'{role}.{name}: unsupported value')
        if (value - c['min']) % max(1, c.get('step', 1)):
            raise SystemExit(f'{role}.{name}: unsupported step')
        if c['choices'] is not None and value not in c['choices']:
            raise SystemExit(f'{role}.{name}: unsupported menu choice')
PY
install -d -m 0700 "$BACKUP"
cp -a "$CONFIG" "$BACKUP/camera-controls.yaml"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
cp -a /run/hpr-video/video-status.json "$BACKUP/video-status.json"
systemctl cat hpr-video-compositor.service > "$BACKUP/video-unit.txt"
TEMP_PROFILE="$(mktemp /etc/hpr/.camera-controls.XXXXXX)"
install -o "$(stat -c %U "$CONFIG")" -g "$(stat -c %G "$CONFIG")" -m "$(stat -c %a "$CONFIG")" "$PROFILE" "$TEMP_PROFILE"
cmp "$CONFIG" "$BACKUP/camera-controls.yaml"
mv "$TEMP_PROFILE" "$CONFIG"
restore_on_failure() {
  cp -a "$BACKUP/camera-controls.yaml" "$CONFIG"
  echo "Profile validation failed; original file restored from $BACKUP" >&2
}
trap restore_on_failure ERR
sleep 3
/usr/bin/python3 "$REPO_ROOT/scripts/validate-pi-camera-controls.py"
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
trap - ERR
echo "Camera profile applied without service restarts. Backup: $BACKUP"

#!/usr/bin/env bash
# Targeted, guarded deployment. Do not run the full installer on a live trike.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT=/opt/hpr/gateway
BACKUP="/opt/hpr/backups/camera-controls-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
/usr/bin/python3 - "$INSTALL_ROOT" "${REPO_ROOT}" <<'PY'
import hashlib, importlib.util, sys
from pathlib import Path
root, repo = map(Path, sys.argv[1:])
expected = 'b7266af30fc857e9413e5aed8442cc82cefbae1184f963ae288ff39a902f3191'
for relative in ('bin/hpr-video-native.py', 'runtime/video/hpr-video-native.py'):
    target = root / relative
    actual = hashlib.sha256(target.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if actual != expected:
        raise SystemExit(f'Unexpected live file {target}: {actual}; refusing overwrite')
spec = importlib.util.spec_from_file_location('video', repo / 'bin/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)
local = Path('/etc/hpr/camera-controls.yaml')
video.load_picture_controls(local if local.exists() else repo / 'config/camera-controls.example.yaml')
PY
command -v v4l2-ctl >/dev/null
systemctl is-active --quiet hpr-video-compositor.service
install -d -m 0700 "$BACKUP"
cp -a "$INSTALL_ROOT/bin/hpr-video-native.py" "$BACKUP/native-bin.py"
cp -a "$INSTALL_ROOT/runtime/video/hpr-video-native.py" "$BACKUP/native-runtime.py"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat hpr-video-compositor.service > "$BACKUP/unit.txt"
cp -a "$REPO_ROOT/scripts/rollback-pi-camera-controls.sh" "$BACKUP/rollback.sh"
if [ -e /etc/hpr/camera-controls.yaml ]; then
  cp -a /etc/hpr/camera-controls.yaml "$BACKUP/camera-controls.yaml"
else
  touch "$BACKUP/controls-was-absent"
fi
restore_on_failure() { bash "$BACKUP/rollback.sh" "$BACKUP"; }
trap restore_on_failure ERR
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$INSTALL_ROOT/bin/hpr-video-native.py"
install -m 0755 "$REPO_ROOT/bin/hpr-video-native.py" "$INSTALL_ROOT/runtime/video/hpr-video-native.py"
if [ ! -e /etc/hpr/camera-controls.yaml ]; then
  group="$(systemctl show -p Group --value hpr-video-compositor.service)"
  install -o root -g "$group" -m 0640 "$REPO_ROOT/config/camera-controls.example.yaml" /etc/hpr/camera-controls.yaml
fi
systemctl restart hpr-video-compositor.service
sleep 2
systemctl is-active --quiet hpr-video-compositor.service
trap - ERR
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
echo "Camera controls deployed; only video compositor restarted. Backup: $BACKUP"

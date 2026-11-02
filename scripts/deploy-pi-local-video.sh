#!/usr/bin/env bash
# Migrate the approved runtime trial to persistent local quality; no central edit.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT=/opt/hpr/gateway
BACKUP="/opt/hpr/backups/local-video-$(date -u +%Y%m%dT%H%M%SZ)"
DROPIN=/run/systemd/system/hpr-video-compositor.service.d/90-stream-trial.conf
TRIAL=/run/hpr-video-stream-trial/trial.json
# Validate against GitHub's pre-change startup source and the exact known trial.
python3 - "$REPO_ROOT" <<'PY'
import importlib.util, json, sys
from pathlib import Path
repo = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('trial', repo / 'bin/hpr-video-stream-trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
assert trial.DROPIN.read_text() == trial.DROPIN_TEXT, 'Unexpected live drop-in'
state = json.loads(trial.STATE.read_text())
assert state['overrides'] == {'stream_size': '800x450', 'bitrate': '1500k'}, 'Unexpected trial settings'
for path in ('/etc/hpr/video-local.yaml', '/opt/hpr/gateway/hpr_gateway/video_local.py'):
    assert not Path(path).exists(), f'Unidentified existing {path}; refusing overwrite'
old = b'''#!/usr/bin/env bash
set -euo pipefail
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
# Read through the supported central-config loader using the gateway venv.
# OS Python provides the packaged GStreamer introspection bindings.
HPR_VIDEO_SETTINGS="$(PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" -c \\
  'import json,sys; from hpr_gateway.config import load_config; print(json.dumps(load_config(sys.argv[1]).get("video", {})))' "$CONFIG_PATH")"
export HPR_VIDEO_SETTINGS
exec /usr/bin/python3 "${INSTALL_ROOT}/runtime/video/hpr-video-native.py"
'''
for path in ('bin/hpr-video-compose-gst.sh', 'runtime/video/hpr-video-compose-gst.sh'):
    assert (trial.ROOT / path).read_bytes().replace(b'\r\n', b'\n') == old, f'Unexpected live {path}'
PY
PYTHONPATH="$REPO_ROOT" "$INSTALL_ROOT/venv/bin/python" - "$REPO_ROOT/config/video-local.front-800x450.yaml" <<'PY'
import sys
from hpr_gateway.config import load_config
from hpr_gateway.video_local import read_local, merge_local
merge_local(load_config('/etc/hpr/hpr.yaml').get('video', {}), read_local(sys.argv[1]))
PY
install -d -m 0700 "$BACKUP"
cp -a "$INSTALL_ROOT/bin/hpr-video-compose-gst.sh" "$BACKUP/gst-bin.sh"
cp -a "$INSTALL_ROOT/runtime/video/hpr-video-compose-gst.sh" "$BACKUP/gst-runtime.sh"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
cp -a /etc/hpr/camera-controls.yaml "$BACKUP/camera-controls.yaml"
cp -a "$DROPIN" "$BACKUP/trial-dropin.conf"
cp -a "$TRIAL" "$BACKUP/trial-state.json"
systemctl cat hpr-video-compositor.service > "$BACKUP/unit-before.txt"
restore_on_failure() {
  cp -a "$BACKUP/gst-bin.sh" "$INSTALL_ROOT/bin/hpr-video-compose-gst.sh"
  cp -a "$BACKUP/gst-runtime.sh" "$INSTALL_ROOT/runtime/video/hpr-video-compose-gst.sh"
  cp -a "$BACKUP/trial-dropin.conf" "$DROPIN"
  cp -a "$BACKUP/trial-state.json" "$TRIAL"
  # Preserve our newly introduced local files instead of deleting them.
  if [ -e /etc/hpr/video-local.yaml ]; then mv /etc/hpr/video-local.yaml "$BACKUP/failed-video-local.yaml"; fi
  if [ -e "$INSTALL_ROOT/hpr_gateway/video_local.py" ]; then mv "$INSTALL_ROOT/hpr_gateway/video_local.py" "$BACKUP/failed-video-local.py"; fi
  systemctl daemon-reload
  systemctl restart hpr-video-compositor.service
}
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/video_local.py" "$INSTALL_ROOT/hpr_gateway/video_local.py"
install -m 0755 "$REPO_ROOT/bin/hpr-video-compose-gst.sh" "$INSTALL_ROOT/bin/hpr-video-compose-gst.sh"
install -m 0755 "$REPO_ROOT/bin/hpr-video-compose-gst.sh" "$INSTALL_ROOT/runtime/video/hpr-video-compose-gst.sh"
GROUP="$(systemctl show hpr-video-compositor.service -p Group --value)"
install -o root -g "$GROUP" -m 0640 "$REPO_ROOT/config/video-local.front-800x450.yaml" /etc/hpr/video-local.yaml
mv "$DROPIN" "$BACKUP/retired-trial-dropin.conf"
systemctl daemon-reload
systemctl restart hpr-video-compositor.service
PYTHONPATH="$INSTALL_ROOT" "$INSTALL_ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-local-video.py"
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
cmp /etc/hpr/camera-controls.yaml "$BACKUP/camera-controls.yaml"
mv "$TRIAL" "$BACKUP/retired-trial-state.json"
trap - ERR
echo "Permanent local video quality deployed; only video supervisor restarted. Backup: $BACKUP"

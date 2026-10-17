#!/usr/bin/env bash
# Merge an approved local-quality profile, preserving every unmentioned setting.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
PROFILE="$(realpath "${1:?Supply a local-video YAML profile}")"
CONFIG=/etc/hpr/video-local.yaml
INSTALL_ROOT=/opt/hpr/gateway
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP="/opt/hpr/backups/local-video-quality-$(date -u +%Y%m%dT%H%M%SZ)"
[[ -f "$CONFIG" ]] || { echo 'Missing persistent local video configuration' >&2; exit 1; }
systemctl is-active --quiet hpr-video-compositor.service
install -d -m 0700 "$BACKUP"
cp -a "$CONFIG" "$BACKUP/video-local.yaml"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
cp -a /etc/hpr/camera-controls.yaml "$BACKUP/camera-controls.yaml"
systemctl cat hpr-video-compositor.service > "$BACKUP/unit.txt"
TEMP_PROFILE="$(mktemp /etc/hpr/.video-local.XXXXXX)"
PYTHONPATH="$INSTALL_ROOT" "$INSTALL_ROOT/venv/bin/python" - "$PROFILE" "$TEMP_PROFILE" <<'PY'
import sys, yaml
from pathlib import Path
from hpr_gateway.config import load_config
from hpr_gateway.video_local import read_local, merge_local
central = load_config('/etc/hpr/hpr.yaml').get('video', {})
current, updates = read_local('/etc/hpr/video-local.yaml'), read_local(sys.argv[1])
merge_local(central, updates)  # Validate keys/types before merging.
for role, values in updates.items():
    current.setdefault(role, {}).update(values)
merge_local(central, current)
Path(sys.argv[2]).write_text('# Persistent Pi-local stream quality; unmentioned settings stay central.\n' + yaml.safe_dump(current, sort_keys=False))
PY
chown "$(stat -c %U "$CONFIG"):$(stat -c %G "$CONFIG")" "$TEMP_PROFILE"
chmod "$(stat -c %a "$CONFIG")" "$TEMP_PROFILE"
cmp "$CONFIG" "$BACKUP/video-local.yaml"
restore_on_failure() {
  cp -a "$CONFIG" "$BACKUP/failed-video-local.yaml"
  cp -a "$BACKUP/video-local.yaml" "$CONFIG"
  systemctl restart hpr-video-compositor.service
  echo "Validation failed; previous local video settings restored from $BACKUP" >&2
}
trap restore_on_failure ERR
mv "$TEMP_PROFILE" "$CONFIG"
systemctl restart hpr-video-compositor.service
PYTHONPATH="$INSTALL_ROOT" "$INSTALL_ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-local-video.py"
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
cmp /etc/hpr/camera-controls.yaml "$BACKUP/camera-controls.yaml"
trap - ERR
echo "Local stream profile applied; only video supervisor restarted. Backup: $BACKUP"

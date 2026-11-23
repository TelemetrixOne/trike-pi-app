#!/usr/bin/env bash
# Scoped HDMI retirement, after a fresh main/live comparison and idle admission.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "${1:-}" == --video-restart-approved && "${2:-}" =~ ^[a-f0-9]{64}$ ]] || {
  echo 'Usage: sudo scripts/deploy-pi-stream-only.sh --video-restart-approved REVIEWED_COMPOSITOR_UNIT_SHA256 [REVIEWED_CONSOLE_SHA256] [REVIEWED_LEGACY_DISPLAY_SHA256]' >&2; exit 2;
}
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
UNIT=/etc/systemd/system/hpr-video-compositor.service
CONSOLE=/etc/systemd/system/hpr-video-console.service
[[ "$(id -u)" == 0 ]]
printf '%s  %s\n' "$2" "$UNIT" | sha256sum -c -
# Never replace an unidentified live enhancement. Runtime copies must match too.
python3 - "$REPO" "$ROOT" <<'PY'
import hashlib,json,sys
from pathlib import Path
repo,root=map(Path,sys.argv[1:])
for relative,baseline in json.loads((repo/'config/video-stream-only-baseline.json').read_text()).items():
    for name in [relative]+(['runtime/video/'+Path(relative).name] if relative.startswith('bin/') else []):
        target=root/name
        if target.is_symlink(): raise SystemExit('Refusing symlink: '+name)
        old=target.read_bytes()
        if hashlib.sha256(old).hexdigest()!=baseline and old!=(repo/relative).read_bytes():
            raise SystemExit('Preserve and inspect unexpected live file: '+name)
PY
if [[ -d "$UNIT.d" ]] && find "$UNIT.d" -mindepth 1 -maxdepth 1 ! -name 90-private-shm.conf | grep -q .; then
  echo 'Unidentified compositor drop-ins; preserve and inspect first' >&2; exit 1
fi
index=3
for retired in "$CONSOLE" /etc/systemd/system/hpr-video-display.service; do
  if [[ -e "$retired" || -L "$retired" ]]; then
    [[ "${!index:-}" =~ ^[a-f0-9]{64}$ && ! -L "$retired" ]] || { echo "Review $retired and supply its SHA256" >&2; exit 1; }
    printf '%s  %s\n' "${!index}" "$retired" | sha256sum -c -
  fi
  index=$((index+1))
  # Unit contents must be reviewed separately; refuse local drop-ins automatically.
  [[ ! -d "$retired.d" ]] || { echo "Inspect and preserve $retired.d first" >&2; exit 1; }
done
BACKUP="/opt/hpr/backups/stream-only-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
for retired in "$CONSOLE" /etc/systemd/system/hpr-video-display.service; do
  if [[ -e "$retired" ]]; then
    cp -a "$retired" "$BACKUP/"
  fi
done
systemctl cat hpr-video-compositor.service > "$BACKUP/compositor-unit.txt"
if [[ -L /etc/systemd/system/getty@tty1.service ]]; then
  [[ "$(readlink /etc/systemd/system/getty@tty1.service)" == /dev/null ]] || {
    echo 'Unidentified tty1 override; inspect first' >&2; exit 1;
  }
  cp -a /etc/systemd/system/getty@tty1.service "$BACKUP/"
fi
# Stage the backed-up runtime before retiring the console dependency.
HPR_START_SERVICES=false bash "$REPO/scripts/deploy-pi-video-uplift.sh"
for retired in "$CONSOLE" /etc/systemd/system/hpr-video-display.service; do
  if [[ -f "$retired" ]]; then
    systemctl disable --now "$(basename "$retired")"
    rm -- "$retired"
  fi
done
if [[ -L /etc/systemd/system/getty@tty1.service ]]; then
  systemctl unmask getty@tty1.service
fi
systemctl daemon-reload
systemctl restart hpr-video-compositor.service hpr-services-health.service hpr-trike-config-sync.service
systemctl is-active --quiet hpr-video-compositor.service hpr-services-health.service hpr-trike-config-sync.service
printf 'Stream-only activation complete. Console backup: %s\nRun scripts/validate-pi-gateway.sh and inspect both feeds.\n' "$BACKUP"

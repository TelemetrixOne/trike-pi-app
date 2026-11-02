#!/usr/bin/env bash
# Native deployment acceptance switch. Does not affect HDMI or camera settings.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
PERMANENT=/etc/systemd/system/hpr-video-compositor.service.d/80-video-timestamp.conf
TRIAL=/run/systemd/system/hpr-video-compositor.service.d/90-video-timestamp-trial.conf
MODE="${1:?Supply off, trial or enable}"
[[ -f "$PERMANENT" ]] || { echo 'Install guarded timestamp update first' >&2; exit 1; }
grep -qx '\[Service\]' "$PERMANENT"
grep -qx 'Environment=HPR_VIDEO_TIMESTAMP_ENABLED=[01]' "$PERMANENT"
[[ "$(wc -l < "$PERMANENT")" == 2 ]] || { echo 'Unexpected timestamp drop-in; stop' >&2; exit 1; }
if [[ -e "$TRIAL" ]]; then
  grep -qx 'Environment=HPR_VIDEO_TIMESTAMP_ENABLED=1' "$TRIAL"
  [[ "$(wc -l < "$TRIAL")" == 2 ]] || { echo 'Unexpected trial drop-in; stop' >&2; exit 1; }
fi
case "$MODE" in
  off)
    printf '[Service]\nEnvironment=HPR_VIDEO_TIMESTAMP_ENABLED=0\n' > "$PERMANENT"
    [[ ! -e "$TRIAL" ]] || rm -- "$TRIAL"
    ;;
  trial|enable)
    [[ "$(timedatectl show -p NTPSynchronized --value)" == yes ]] || { echo 'Clock is not synchronised; no enablement' >&2; exit 1; }
    timedatectl timesync-status --no-pager
    HPR_VIDEO_TIMESTAMP_ENABLED=1 PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" \
      -m hpr_gateway.video_timestamp --config /etc/hpr/hpr.yaml
    gst-inspect-1.0 clockoverlay >/dev/null
    [[ "$(fc-match -f '%{family}' 'DejaVu Sans')" == *'DejaVu Sans'* ]] || { echo 'Missing expected font' >&2; exit 1; }
    if [[ "$MODE" == enable ]]; then
      [[ -z "$(systemctl list-units --type=timer --state=active --no-legend 'hpr-video-timestamp-revert-*.timer')" ]] || { echo 'Wait for the trial rollback timer to finish before permanent enablement' >&2; exit 1; }
      REVIEW=()
      if [[ "${4:-}" == --accept-unmeasured-latency ]]; then
        REVIEW=(--accept-unmeasured-latency)
      elif [[ "$#" -gt 3 ]]; then
        echo 'Unknown acceptance option' >&2; exit 1
      fi
      PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$REPO_ROOT/scripts/check-video-timestamp-performance.py" "${2:?baseline JSON}" "${3:?trial JSON}" "${REVIEW[@]}"
      BACKUP="/opt/hpr/backups/video-timestamp-enable-$(date -u +%Y%m%dT%H%M%SZ)"
      install -d -m 0700 "$BACKUP"
      cp -a "$PERMANENT" "$BACKUP/80-video-timestamp.conf"
      if [[ "${#REVIEW[@]}" != 0 ]]; then
        logger -t hpr-video-timestamp 'Operator approved permanent enablement with end-to-end latency still unmeasured'
      fi
      printf '[Service]\nEnvironment=HPR_VIDEO_TIMESTAMP_ENABLED=1\n' > "$PERMANENT"
      [[ ! -e "$TRIAL" ]] || rm -- "$TRIAL"
    else
      [[ ! -e "$TRIAL" ]] || { echo 'Trial already running' >&2; exit 1; }
      [[ "$(tail -1 "$PERMANENT")" == Environment=HPR_VIDEO_TIMESTAMP_ENABLED=0 ]] || { echo 'Baseline must be OFF' >&2; exit 1; }
      # Schedule automatic rollback before enabling. Runtime override also
      # disappears on reboot. No Pi reboot or unrelated restart is requested.
      systemd-run --quiet --unit="hpr-video-timestamp-revert-$(date +%s)" --on-active=300s \
        /bin/bash "$REPO_ROOT/scripts/video-timestamp-mode.sh" off
      install -d "$(dirname "$TRIAL")"
      printf '[Service]\nEnvironment=HPR_VIDEO_TIMESTAMP_ENABLED=1\n' > "$TRIAL"
      echo 'Trial automatically returns OFF in 5 minutes; do not enable permanently before rollback fires.'
    fi
    ;;
  *) echo 'Expected off, trial or enable' >&2; exit 1;;
esac
systemctl daemon-reload
systemctl restart hpr-video-compositor.service
if ! PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-local-video.py"; then
  printf '[Service]\nEnvironment=HPR_VIDEO_TIMESTAMP_ENABLED=0\n' > "$PERMANENT"
  [[ ! -e "$TRIAL" ]] || rm -- "$TRIAL"
  systemctl daemon-reload
  systemctl restart hpr-video-compositor.service
  echo 'Validation failed; timestamp restored OFF' >&2
  exit 1
fi

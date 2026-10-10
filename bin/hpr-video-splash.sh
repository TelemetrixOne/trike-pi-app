#!/usr/bin/env bash
# Best-effort early boot image. The native KMS camera viewer takes over later.
set -euo pipefail

INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
IMAGE="${INSTALL_ROOT}/runtime/video/startup-splash.png"
FB="${HPR_DISPLAY_DEVICE:-/dev/fb0}"

[[ -f "$IMAGE" && -e "$FB" ]] || exit 0
[[ "$(cat /sys/class/graphics/"$(basename "$FB")"/bits_per_pixel 2>/dev/null)" == 16 ]] || exit 0
shopt -s nullglob
connected=false
for status in /sys/class/drm/card*-HDMI-A-*/status; do
  if [[ "$(cat "$status")" == connected ]]; then connected=true; break; fi
done
shopt -u nullglob
[[ "$connected" == true ]] || exit 0

size="$(cat /sys/class/graphics/"$(basename "$FB")"/virtual_size)"
[[ "$size" =~ ^([0-9]+),([0-9]+)$ ]] || exit 0
width="${BASH_REMATCH[1]}" height="${BASH_REMATCH[2]}"
# Scale proportionally and pad: never stretch or crop the supplied artwork.
timeout 5 /usr/bin/ffmpeg -nostdin -y -hide_banner -loglevel error \
  -i "$IMAGE" \
  -vf "scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:black,format=rgb565le" \
  -frames:v 1 -f fbdev "$FB" || true
exit 0

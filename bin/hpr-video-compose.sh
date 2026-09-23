#!/usr/bin/env bash
set -euo pipefail
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
cfg() { PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value --config "${CONFIG_PATH}" "$@"; }
cam() { cfg "video.cameras.${1}.${2}" --default "${3}"; }
rotation_filter() {
  case "$1" in none) printf '';; clockwise_90) printf 'transpose=clock,';;
    180) printf 'hflip,vflip,';; anticlockwise_90) printf 'transpose=cclock,';;
    *) echo "Unsupported camera rotation: $1" >&2; return 1;; esac
}
MAIN="$(cfg video.display.camera --default front)"; PIP="$(cfg video.display.picture_in_picture.camera --default rear)"
DISPLAY_DEVICE="$(cfg video.display.device --default /dev/fb0)"; PIXEL_FORMAT="$(cfg video.display.pixel_format --default rgb565le)"
CAPTURE_SIZE="$(cfg video.display.capture_size --default 1280x720)"; CAPTURE_FPS="$(cfg video.display.capture_framerate --default 30)"
INPUT_QUEUE_SIZE="$(cfg video.display.input_queue_size --default 2)"
PIP_PC="$(cfg video.display.picture_in_picture.width_percent --default 25)"; MARGIN="$(cfg video.display.picture_in_picture.margin_pixels --default 24)"
MD="$(cam "$MAIN" device '')"; PD="$(cam "$PIP" device '')"; MP="$(cam "$MAIN" path "$MAIN")"; PP="$(cam "$PIP" path "$PIP")"
MS="$(cam "$MAIN" stream_size 640x360)"; PS="$(cam "$PIP" stream_size 640x360)"
MSW="${MS%x*}"; MSH="${MS#*x}"; PSW="${PS%x*}"; PSH="${PS#*x}"
MF="$(cam "$MAIN" output_framerate 25)"; PF="$(cam "$PIP" output_framerate 25)"; MB="$(cam "$MAIN" bitrate 700k)"; PB="$(cam "$PIP" bitrate 700k)"
MG="$(cam "$MAIN" gop 25)"; PG="$(cam "$PIP" gop 25)"; MR="$(rotation_filter "$(cam "$MAIN" rotation none)")"; PR="$(rotation_filter "$(cam "$PIP" rotation none)")"
[[ -n "$MD" && -n "$PD" && "$MD" != "$PD" ]] || { echo 'Cameras need distinct stable device paths' >&2; exit 10; }

# USB port paths are preferred because the installed cameras expose the same
# vendor, product and serial number. If a preferred port disappears, preserve
# every role that is still identifiable and assign the unclaimed camera to the
# missing role. This survives moving one camera without relying on /dev/videoN.
resolve_cameras() {
  local configured_main="$1" configured_pip="$2" main_real='' pip_real='' path real
  local -a candidates=()
  local -A seen=()
  MD=''; PD=''
  if [[ -e "$configured_main" ]]; then MD="$configured_main"; main_real="$(readlink -f -- "$MD")"; fi
  if [[ -e "$configured_pip" ]]; then PD="$configured_pip"; pip_real="$(readlink -f -- "$PD")"; fi
  if [[ -n "$main_real" && "$main_real" == "$pip_real" ]]; then PD=''; pip_real=''; fi
  shopt -s nullglob
  for path in /dev/v4l/by-path/*usbv2*video-index0; do
    real="$(readlink -f -- "$path")" || continue
    [[ -n "${seen[$real]:-}" ]] && continue
    seen[$real]=1; candidates+=("$path")
  done
  shopt -u nullglob
  if [[ -z "$MD" ]]; then
    for path in "${candidates[@]}"; do
      real="$(readlink -f -- "$path")"
      [[ -n "$pip_real" && "$real" == "$pip_real" ]] && continue
      MD="$path"; main_real="$real"; break
    done
    [[ -z "$MD" ]] || echo "Configured ${MAIN} camera is unavailable; using discovered device ${MD}." >&2
  fi
  if [[ -z "$PD" ]]; then
    for path in "${candidates[@]}"; do
      real="$(readlink -f -- "$path")"
      [[ -n "$main_real" && "$real" == "$main_real" ]] && continue
      PD="$path"; pip_real="$real"; break
    done
    [[ -z "$PD" ]] || echo "Configured ${PIP} camera is unavailable; using discovered device ${PD}." >&2
  fi
}
CONFIGURED_MD="$MD"; CONFIGURED_PD="$PD"
while true; do resolve_cameras "$CONFIGURED_MD" "$CONFIGURED_PD"; [[ -n "$MD" || -n "$PD" ]] && break; echo 'Waiting for any USB camera' >&2; sleep 5; done

hdmi_connected() {
  local connector
  [[ -e "$DISPLAY_DEVICE" ]] || return 1
  shopt -s nullglob
  for connector in /sys/class/drm/card*-HDMI-A-*/status; do
    [[ "$(<"$connector")" == connected ]] && { shopt -u nullglob; return 0; }
  done
  shopt -u nullglob
  return 1
}

# Build a correctly oriented landscape frame first. Scale-to-fill then removes
# only the destination-aspect edges needed for HDMI or the web stream.
INPUTS=(); DISPLAY_OUTPUT=(); FRONT_OUTPUT=(); REAR_OUTPUT=(); MAIN_INDEX=''; PIP_INDEX=''; NEXT_INDEX=0
if [[ -n "$MD" ]]; then
  MAIN_INDEX="$NEXT_INDEX"; NEXT_INDEX=$((NEXT_INDEX+1))
  INPUTS+=(-thread_queue_size "$INPUT_QUEUE_SIZE" -f v4l2 -input_format mjpeg -video_size "$CAPTURE_SIZE" -framerate "$CAPTURE_FPS" -i "$MD")
fi
if [[ -n "$PD" ]]; then
  PIP_INDEX="$NEXT_INDEX"; NEXT_INDEX=$((NEXT_INDEX+1))
  INPUTS+=(-thread_queue_size "$INPUT_QUEUE_SIZE" -f v4l2 -input_format mjpeg -video_size "$CAPTURE_SIZE" -framerate "$CAPTURE_FPS" -i "$PD")
fi

if hdmi_connected; then
  FB_NAME="$(basename "$DISPLAY_DEVICE")"
  FB_SIZE_PATH="/sys/class/graphics/${FB_NAME}/virtual_size"
  DS="$(tr ',' 'x' <"$FB_SIZE_PATH")"; [[ "$DS" =~ ^[0-9]+x[0-9]+$ ]] || exit 12
  DW="${DS%x*}"; DH="${DS#*x}"; PW=$((DW*PIP_PC/100)); PH=$((PW*9/16))
  if [[ -n "$MAIN_INDEX" && -n "$PIP_INDEX" ]]; then
    F="[${MAIN_INDEX}:v]split=2[mn0][md0];[${PIP_INDEX}:v]split=2[pn0][pd0];"
    F+="[mn0]${MR}fps=${MF},scale=${MSW}:${MSH}:force_original_aspect_ratio=increase,crop=${MSW}:${MSH},setsar=1[mn];"
    F+="[pn0]${PR}fps=${PF},scale=${PSW}:${PSH}:force_original_aspect_ratio=increase,crop=${PSW}:${PSH},setsar=1[pn];"
    F+="[md0]${MR}scale=${DW}:${DH}:force_original_aspect_ratio=increase,crop=${DW}:${DH},setsar=1[base];"
    F+="[pd0]${PR}scale=${PW}:${PH}:force_original_aspect_ratio=increase,crop=${PW}:${PH},setsar=1[inset];"
    F+="[base][inset]overlay=x=W-w-${MARGIN}:y=H-h-${MARGIN}:shortest=0:repeatlast=1:eof_action=repeat[display]"
  elif [[ -n "$MAIN_INDEX" ]]; then
    F="[${MAIN_INDEX}:v]split=2[mn0][md0];[mn0]${MR}fps=${MF},scale=${MSW}:${MSH}:force_original_aspect_ratio=increase,crop=${MSW}:${MSH},setsar=1[mn];[md0]${MR}scale=${DW}:${DH}:force_original_aspect_ratio=increase,crop=${DW}:${DH},setsar=1[display]"
  else
    F="[${PIP_INDEX}:v]split=2[pn0][pd0];[pn0]${PR}fps=${PF},scale=${PSW}:${PSH}:force_original_aspect_ratio=increase,crop=${PSW}:${PSH},setsar=1[pn];[pd0]${PR}scale=${DW}:${DH}:force_original_aspect_ratio=increase,crop=${DW}:${DH},setsar=1[display]"
  fi
  DISPLAY_OUTPUT=(-map '[display]' -c:v rawvideo -pix_fmt "$PIXEL_FORMAT" -f fbdev "$DISPLAY_DEVICE")
  echo "HDMI-priority mode: ${CAPTURE_SIZE}@${CAPTURE_FPS} -> native ${DS}; input queues ${INPUT_QUEUE_SIZE}." >&2
else
  F=''
  [[ -z "$MAIN_INDEX" ]] || F+="[${MAIN_INDEX}:v]${MR}fps=${MF},scale=${MSW}:${MSH}:force_original_aspect_ratio=increase,crop=${MSW}:${MSH},setsar=1[mn];"
  [[ -z "$PIP_INDEX" ]] || F+="[${PIP_INDEX}:v]${PR}fps=${PF},scale=${PSW}:${PSH}:force_original_aspect_ratio=increase,crop=${PSW}:${PSH},setsar=1[pn];"
  F="${F%;}"
  echo "Streaming-priority mode: HDMI is disconnected or ${DISPLAY_DEVICE} is unavailable." >&2
fi
[[ -z "$MAIN_INDEX" ]] || FRONT_OUTPUT=(-map '[mn]' -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -b:v "$MB" -maxrate "$MB" -bufsize 140k -g "$MG" -keyint_min "$MG" -sc_threshold 0 -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/$MP")
[[ -z "$PIP_INDEX" ]] || REAR_OUTPUT=(-map '[pn]' -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -b:v "$PB" -maxrate "$PB" -bufsize 140k -g "$PG" -keyint_min "$PG" -sc_threshold 0 -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/$PP")

/usr/bin/ffmpeg -hide_banner -nostdin -fflags nobuffer -flags low_delay "${INPUTS[@]}" \
  -filter_complex_threads 4 -filter_complex "$F" -an \
  "${DISPLAY_OUTPUT[@]}" "${FRONT_OUTPUT[@]}" "${REAR_OUTPUT[@]}" &
FFMPEG_PID=$!
INITIAL_HARDWARE="$(find /dev/v4l/by-path -maxdepth 1 -type l -name '*usbv2*video-index0' -printf '%l\n' 2>/dev/null | sort; grep -h . /sys/class/drm/card*-HDMI-A-*/status 2>/dev/null || true)"
(
  while sleep 2; do
    CURRENT_HARDWARE="$(find /dev/v4l/by-path -maxdepth 1 -type l -name '*usbv2*video-index0' -printf '%l\n' 2>/dev/null | sort; grep -h . /sys/class/drm/card*-HDMI-A-*/status 2>/dev/null || true)"
    [[ "$CURRENT_HARDWARE" == "$INITIAL_HARDWARE" ]] || { echo 'Camera or HDMI state changed; rebuilding video mode.' >&2; kill -TERM "$FFMPEG_PID" 2>/dev/null || true; exit; }
  done
) &
WATCHER_PID=$!
set +e; wait "$FFMPEG_PID"; STATUS=$?; set -e
kill "$WATCHER_PID" 2>/dev/null || true; wait "$WATCHER_PID" 2>/dev/null || true
exit "$STATUS"

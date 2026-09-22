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
for d in "$MD" "$PD"; do while [[ ! -e "$d" ]]; do echo "Waiting for $d" >&2; sleep 5; done; done

# Build a correctly oriented landscape frame first. Scale-to-fill then removes
# only the destination-aspect edges needed for HDMI or the web stream.
DISPLAY_OUTPUT=()
if [[ -e "$DISPLAY_DEVICE" ]]; then
  FB_NAME="$(basename "$DISPLAY_DEVICE")"
  FB_SIZE_PATH="/sys/class/graphics/${FB_NAME}/virtual_size"
  DS="$(tr ',' 'x' <"$FB_SIZE_PATH")"; [[ "$DS" =~ ^[0-9]+x[0-9]+$ ]] || exit 12
  DW="${DS%x*}"; DH="${DS#*x}"; PW=$((DW*PIP_PC/100)); PH=$((PW*9/16))
  F="[0:v]split=2[mn0][md0];[1:v]split=2[pn0][pd0];"
  F+="[mn0]${MR}fps=${MF},scale=${MSW}:${MSH}:force_original_aspect_ratio=increase,crop=${MSW}:${MSH},setsar=1[mn];"
  F+="[pn0]${PR}fps=${PF},scale=${PSW}:${PSH}:force_original_aspect_ratio=increase,crop=${PSW}:${PSH},setsar=1[pn];"
  F+="[md0]${MR}scale=${DW}:${DH}:force_original_aspect_ratio=increase,crop=${DW}:${DH},setsar=1[base];"
  F+="[pd0]${PR}scale=${PW}:${PH}:force_original_aspect_ratio=increase,crop=${PW}:${PH},setsar=1[inset];"
  # The rider view follows the front camera clock. A stopped rear camera must not
  # terminate the display, and only the most recent rear frame is repeated.
  F+="[base][inset]overlay=x=W-w-${MARGIN}:y=H-h-${MARGIN}:shortest=0:repeatlast=1:eof_action=repeat[display]"
  DISPLAY_OUTPUT=(-map '[display]' -c:v rawvideo -pix_fmt "$PIXEL_FORMAT" -f fbdev "$DISPLAY_DEVICE")
  echo "Low-latency framebuffer compositor: ${CAPTURE_SIZE}@${CAPTURE_FPS} -> native ${DS}; PiP ${PW}x${PH}; input queues ${INPUT_QUEUE_SIZE}." >&2
else
  F="[0:v]${MR}fps=${MF},scale=${MSW}:${MSH}:force_original_aspect_ratio=increase,crop=${MSW}:${MSH},setsar=1[mn];"
  F+="[1:v]${PR}fps=${PF},scale=${PSW}:${PSH}:force_original_aspect_ratio=increase,crop=${PSW}:${PSH},setsar=1[pn]"
  echo "Display device ${DISPLAY_DEVICE} is unavailable; publishing front and rear streams without HDMI output." >&2
fi
exec /usr/bin/ffmpeg -hide_banner -nostdin -fflags nobuffer -flags low_delay \
  -thread_queue_size "$INPUT_QUEUE_SIZE" -f v4l2 -input_format mjpeg -video_size "$CAPTURE_SIZE" -framerate "$CAPTURE_FPS" -i "$MD" \
  -thread_queue_size "$INPUT_QUEUE_SIZE" -f v4l2 -input_format mjpeg -video_size "$CAPTURE_SIZE" -framerate "$CAPTURE_FPS" -i "$PD" \
  -filter_complex_threads 4 -filter_complex "$F" -an \
  -map '[mn]' -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -b:v "$MB" -maxrate "$MB" -bufsize 140k -g "$MG" -keyint_min "$MG" -sc_threshold 0 -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/$MP" \
  -map '[pn]' -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -b:v "$PB" -maxrate "$PB" -bufsize 140k -g "$PG" -keyint_min "$PG" -sc_threshold 0 -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/$PP" \
  "${DISPLAY_OUTPUT[@]}"

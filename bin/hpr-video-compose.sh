#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"

cfg() {
  PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value \
    --config "${CONFIG_PATH}" "$@"
}

camera_value() {
  local camera="$1" key="$2" default_value="$3"
  cfg "video.cameras.${camera}.${key}" --default "${default_value}"
}

rotation_filter() {
  case "$1" in
    none) printf '' ;;
    clockwise_90) printf 'transpose=clock,' ;;
    180) printf 'hflip,vflip,' ;;
    anticlockwise_90) printf 'transpose=cclock,' ;;
    *) echo "Unsupported camera rotation: $1" >&2; return 1 ;;
  esac
}

MAIN_CAMERA="$(cfg video.display.camera --default front)"
PIP_CAMERA="$(cfg video.display.picture_in_picture.camera --default rear)"
DISPLAY_DEVICE="$(cfg video.display.device --default /dev/fb0)"
DISPLAY_PIXEL_FORMAT="$(cfg video.display.pixel_format --default rgb565le)"
CAPTURE_SIZE="$(cfg video.display.capture_size --default 1920x1080)"
CAPTURE_FPS="$(cfg video.display.capture_framerate --default 30)"
PIP_WIDTH_PERCENT="$(cfg video.display.picture_in_picture.width_percent --default 25)"
PIP_MARGIN="$(cfg video.display.picture_in_picture.margin_pixels --default 24)"

MAIN_DEVICE="$(camera_value "${MAIN_CAMERA}" device '')"
PIP_DEVICE="$(camera_value "${PIP_CAMERA}" device '')"
MAIN_INPUT_FORMAT="$(camera_value "${MAIN_CAMERA}" input_format mjpeg)"
PIP_INPUT_FORMAT="$(camera_value "${PIP_CAMERA}" input_format mjpeg)"
MAIN_PATH="$(camera_value "${MAIN_CAMERA}" path "${MAIN_CAMERA}")"
PIP_PATH="$(camera_value "${PIP_CAMERA}" path "${PIP_CAMERA}")"
MAIN_STREAM_SIZE="$(camera_value "${MAIN_CAMERA}" stream_size 640x480)"
PIP_STREAM_SIZE="$(camera_value "${PIP_CAMERA}" stream_size 640x480)"
MAIN_OUTPUT_FPS="$(camera_value "${MAIN_CAMERA}" output_framerate 25)"
PIP_OUTPUT_FPS="$(camera_value "${PIP_CAMERA}" output_framerate 25)"
MAIN_BITRATE="$(camera_value "${MAIN_CAMERA}" bitrate 700k)"
PIP_BITRATE="$(camera_value "${PIP_CAMERA}" bitrate 700k)"
MAIN_GOP="$(camera_value "${MAIN_CAMERA}" gop 25)"
PIP_GOP="$(camera_value "${PIP_CAMERA}" gop 25)"
MAIN_ROTATION="$(rotation_filter "$(camera_value "${MAIN_CAMERA}" rotation none)")"
PIP_ROTATION="$(rotation_filter "$(camera_value "${PIP_CAMERA}" rotation none)")"

if [ -z "${MAIN_DEVICE}" ] || [ -z "${PIP_DEVICE}" ]; then
  echo "Main and PiP cameras require stable device paths." >&2
  exit 10
fi
if [ "${MAIN_DEVICE}" = "${PIP_DEVICE}" ]; then
  echo "Main and PiP cameras must use different devices." >&2
  exit 11
fi
for device in "${MAIN_DEVICE}" "${PIP_DEVICE}"; do
  while [ ! -e "${device}" ]; do
    echo "Waiting for camera at ${device}" >&2
    sleep 5
  done
done
while [ ! -e "${DISPLAY_DEVICE}" ]; do
  echo "Waiting for display framebuffer at ${DISPLAY_DEVICE}" >&2
  sleep 3
done

DISPLAY_SIZE=""
if [ -r /sys/class/graphics/fb0/virtual_size ]; then
  DISPLAY_SIZE="$(tr ',' 'x' < /sys/class/graphics/fb0/virtual_size)"
fi
if [[ ! "${DISPLAY_SIZE}" =~ ^[0-9]+x[0-9]+$ ]]; then
  echo "Unable to determine framebuffer dimensions." >&2
  exit 12
fi
DISPLAY_WIDTH="${DISPLAY_SIZE%x*}"
DISPLAY_HEIGHT="${DISPLAY_SIZE#*x}"
PIP_WIDTH=$((DISPLAY_WIDTH * PIP_WIDTH_PERCENT / 100))
PIP_HEIGHT=$((PIP_WIDTH * 9 / 16))

FILTER_GRAPH="[0:v]split=2[main_net_src][main_display_src];"
FILTER_GRAPH+="[1:v]split=2[pip_net_src][pip_display_src];"
FILTER_GRAPH+="[main_net_src]${MAIN_ROTATION}fps=${MAIN_OUTPUT_FPS},scale=${MAIN_STREAM_SIZE},setsar=1[main_net];"
FILTER_GRAPH+="[pip_net_src]${PIP_ROTATION}fps=${PIP_OUTPUT_FPS},scale=${PIP_STREAM_SIZE},setsar=1[pip_net];"
FILTER_GRAPH+="[main_display_src]${MAIN_ROTATION}fps=${CAPTURE_FPS},scale=${DISPLAY_WIDTH}:${DISPLAY_HEIGHT}:force_original_aspect_ratio=increase,crop=${DISPLAY_WIDTH}:${DISPLAY_HEIGHT},setsar=1[base];"
FILTER_GRAPH+="[pip_display_src]${PIP_ROTATION}fps=${CAPTURE_FPS},scale=${PIP_WIDTH}:${PIP_HEIGHT}:force_original_aspect_ratio=increase,crop=${PIP_WIDTH}:${PIP_HEIGHT},setsar=1[inset];"
FILTER_GRAPH+="[base][inset]overlay=x=W-w-${PIP_MARGIN}:y=H-h-${PIP_MARGIN}:shortest=1[display]"

echo "Direct HDMI composition: ${MAIN_CAMERA} + ${PIP_CAMERA} PiP, capture ${CAPTURE_SIZE}@${CAPTURE_FPS}, display ${DISPLAY_SIZE}." >&2
exec /usr/bin/ffmpeg \
  -hide_banner -nostdin -fflags nobuffer -flags low_delay \
  -thread_queue_size 32 -f v4l2 -input_format "${MAIN_INPUT_FORMAT}" -video_size "${CAPTURE_SIZE}" -framerate "${CAPTURE_FPS}" -i "${MAIN_DEVICE}" \
  -thread_queue_size 32 -f v4l2 -input_format "${PIP_INPUT_FORMAT}" -video_size "${CAPTURE_SIZE}" -framerate "${CAPTURE_FPS}" -i "${PIP_DEVICE}" \
  -filter_complex "${FILTER_GRAPH}" -an \
  -map "[main_net]" -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -b:v "${MAIN_BITRATE}" -maxrate "${MAIN_BITRATE}" -bufsize 140k -g "${MAIN_GOP}" -keyint_min "${MAIN_GOP}" -sc_threshold 0 \
    -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${MAIN_PATH}" \
  -map "[pip_net]" -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -b:v "${PIP_BITRATE}" -maxrate "${PIP_BITRATE}" -bufsize 140k -g "${PIP_GOP}" -keyint_min "${PIP_GOP}" -sc_threshold 0 \
    -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${PIP_PATH}" \
  -map "[display]" -c:v rawvideo -pix_fmt "${DISPLAY_PIXEL_FORMAT}" -f fbdev "${DISPLAY_DEVICE}"

#!/usr/bin/env bash
set -euo pipefail

CAMERA_NAME="${1:?camera name is required}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"

cfg() {
  PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value \
    --config "${CONFIG_PATH}" "$@"
}

DEVICE="$(cfg "video.cameras.${CAMERA_NAME}.device" --default '')"
PATH_NAME="$(cfg "video.cameras.${CAMERA_NAME}.path" --default "${CAMERA_NAME}")"
INPUT_FORMAT="$(cfg "video.cameras.${CAMERA_NAME}.input_format" --default mjpeg)"
SIZE="$(cfg "video.cameras.${CAMERA_NAME}.video_size" --default 640x480)"
STREAM_SIZE="$(cfg "video.cameras.${CAMERA_NAME}.stream_size" --default 640x480)"
INPUT_FPS="$(cfg "video.cameras.${CAMERA_NAME}.input_framerate" --default 30)"
OUTPUT_FPS="$(cfg "video.cameras.${CAMERA_NAME}.output_framerate" --default 25)"
BITRATE="$(cfg "video.cameras.${CAMERA_NAME}.bitrate" --default 700k)"
GOP="$(cfg "video.cameras.${CAMERA_NAME}.gop" --default 25)"
ROTATION="$(cfg "video.cameras.${CAMERA_NAME}.rotation" --default none)"
DISPLAY_ENABLED="$(cfg video.display.enabled --default false)"
DISPLAY_CAMERA="$(cfg video.display.camera --default front)"
DISPLAY_DEVICE="$(cfg video.display.device --default /dev/fb0)"
DISPLAY_PIXEL_FORMAT="$(cfg video.display.pixel_format --default rgb565le)"
DISPLAY_STARTUP_WAIT="$(cfg video.display.startup_wait_seconds --default 10)"

case "${ROTATION}" in
  none) ROTATION_FILTER="" ;;
  clockwise_90) ROTATION_FILTER="transpose=clock," ;;
  180) ROTATION_FILTER="hflip,vflip," ;;
  anticlockwise_90) ROTATION_FILTER="transpose=cclock," ;;
  *)
    echo "Unsupported rotation for camera ${CAMERA_NAME}: ${ROTATION}" >&2
    exit 13
    ;;
esac

case "${DISPLAY_STARTUP_WAIT}" in
  ''|*[!0-9]*) DISPLAY_STARTUP_WAIT=10 ;;
esac

find_display_size() {
  local connector_status candidate
  for connector_status in /sys/class/drm/card*-HDMI-A-*/status; do
    [ -f "${connector_status}" ] || continue
    [ "$(cat "${connector_status}")" = "connected" ] || continue
    candidate="$(head -n 1 "${connector_status%/status}/modes" 2>/dev/null || true)"
    if [[ "${candidate}" =~ ^[0-9]+x[0-9]+$ ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

if [ -z "${DEVICE}" ]; then
  echo "Camera ${CAMERA_NAME} has no configured stable device path." >&2
  exit 10
fi

while [ ! -e "${DEVICE}" ]; do
  echo "Waiting for camera ${CAMERA_NAME} at ${DEVICE}" >&2
  sleep 5
done

if [ "${DISPLAY_ENABLED}" = "true" ] && [ "${CAMERA_NAME}" = "${DISPLAY_CAMERA}" ]; then
  DISPLAY_SIZE=""
  DISPLAY_WAITED=0
  while [ "${DISPLAY_WAITED}" -le "${DISPLAY_STARTUP_WAIT}" ]; do
    DISPLAY_SIZE="$(find_display_size || true)"
    [ -n "${DISPLAY_SIZE}" ] && break
    [ "${DISPLAY_WAITED}" -ge "${DISPLAY_STARTUP_WAIT}" ] && break
    sleep 1
    DISPLAY_WAITED=$((DISPLAY_WAITED + 1))
  done

  if [ -z "${DISPLAY_SIZE}" ]; then
    echo "No connected HDMI mode detected after ${DISPLAY_STARTUP_WAIT}s; continuing with network stream only." >&2
  elif [ ! -e "${DISPLAY_DEVICE}" ]; then
    echo "Display framebuffer is unavailable at ${DISPLAY_DEVICE}; continuing with network stream only." >&2
  else
    DISPLAY_WIDTH="${DISPLAY_SIZE%x*}"
    DISPLAY_HEIGHT="${DISPLAY_SIZE#*x}"
    DISPLAY_FILTER="${ROTATION_FILTER}fps=${INPUT_FPS},scale=${DISPLAY_WIDTH}:${DISPLAY_HEIGHT}:force_original_aspect_ratio=increase,crop=${DISPLAY_WIDTH}:${DISPLAY_HEIGHT},setsar=1"
    echo "Rendering ${CAMERA_NAME} at HDMI mode ${DISPLAY_SIZE} from camera mode ${SIZE}." >&2

    exec /usr/bin/ffmpeg \
      -hide_banner -nostdin -fflags nobuffer -flags low_delay \
      -f v4l2 -input_format "${INPUT_FORMAT}" -video_size "${SIZE}" \
      -framerate "${INPUT_FPS}" -i "${DEVICE}" -an \
      -map 0:v -vf "${ROTATION_FILTER}fps=${OUTPUT_FPS},scale=${STREAM_SIZE},setsar=1" \
      -c:v libx264 -preset ultrafast -tune zerolatency \
      -pix_fmt yuv420p -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize 140k \
      -g "${GOP}" -keyint_min "${GOP}" -sc_threshold 0 \
      -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${PATH_NAME}" \
      -map 0:v -vf "${DISPLAY_FILTER}" -c:v rawvideo -pix_fmt "${DISPLAY_PIXEL_FORMAT}" \
      -f fbdev "${DISPLAY_DEVICE}"
  fi
fi

exec /usr/bin/ffmpeg \
  -hide_banner -nostdin \
  -f v4l2 -input_format "${INPUT_FORMAT}" -video_size "${SIZE}" \
  -framerate "${INPUT_FPS}" -i "${DEVICE}" -an \
  -vf "${ROTATION_FILTER}fps=${OUTPUT_FPS},scale=${STREAM_SIZE},setsar=1" -c:v libx264 -preset ultrafast -tune zerolatency \
  -pix_fmt yuv420p -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize 140k \
  -g "${GOP}" -keyint_min "${GOP}" -sc_threshold 0 \
  -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${PATH_NAME}"

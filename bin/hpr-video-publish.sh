#!/usr/bin/env bash
set -euo pipefail

CAMERA_NAME="${1:?camera name is required}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
TIMESTAMP_ENV="$(PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" \
  -m hpr_gateway.video_timestamp --config "$CONFIG_PATH")"
eval "$TIMESTAMP_ENV"

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
case "${ROTATION}" in
  none) ROTATION_FILTER="" ;;
  clockwise_90) ROTATION_FILTER="transpose=clock," ;;
  180) ROTATION_FILTER="hflip,vflip," ;;
  anticlockwise_90) ROTATION_FILTER="transpose=cclock," ;;
  *) echo "Unsupported rotation: ${ROTATION}" >&2; exit 13 ;;
esac

if [ -z "${DEVICE}" ]; then
  echo "Camera ${CAMERA_NAME} has no configured stable device path." >&2
  exit 10
fi

while [ ! -e "${DEVICE}" ]; do
  echo "Waiting for camera ${CAMERA_NAME} at ${DEVICE}" >&2
  sleep 5
done

exec /usr/bin/ffmpeg \
  -hide_banner -nostdin \
  -f v4l2 -input_format "${INPUT_FORMAT}" -video_size "${SIZE}" \
  -framerate "${INPUT_FPS}" -i "${DEVICE}" -an \
  -vf "${ROTATION_FILTER}fps=${OUTPUT_FPS},scale=${STREAM_SIZE}:force_original_aspect_ratio=decrease,pad=${STREAM_SIZE}:(ow-iw)/2:(oh-ih)/2,setsar=1${HPR_VIDEO_FF_TIMESTAMP}" -c:v libx264 -preset ultrafast -tune zerolatency \
  -pix_fmt yuv420p -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize 140k \
  -g "${GOP}" -keyint_min "${GOP}" -sc_threshold 0 \
  -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${PATH_NAME}"

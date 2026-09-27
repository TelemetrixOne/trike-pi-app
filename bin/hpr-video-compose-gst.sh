#!/usr/bin/env bash
set -euo pipefail

# KMS gets the direct, low-buffer display path. Network encoders consume a
# separate, lossy shared-memory copy of each camera's compressed MJPEG frames.
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
cfg() { PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value --config "${CONFIG_PATH}" "$@"; }
cam() { cfg "video.cameras.${1}.${2}" --default "${3}"; }
orientation() {
  case "$1" in
    none) printf identity;; 180) printf 180;;
    clockwise_90) printf 90r;; anticlockwise_90) printf 90l;;
    *) echo "Unsupported camera rotation: $1" >&2; return 1;;
  esac
}
bitrate_kbps() {
  case "$1" in
    *k) printf '%s' "${1%k}";;
    *M) printf '%s' "$(( ${1%M} * 1000 ))";;
    *) printf '%s' "$(( $1 / 1000 ))";;
  esac
}

MAIN="$(cfg video.display.camera --default front)"
PIP="$(cfg video.display.picture_in_picture.camera --default rear)"
MD="${HPR_MAIN_DEVICE:-}"; PD="${HPR_PIP_DEVICE:-}"
CAPTURE_SIZE="$(cfg video.display.capture_size --default 1280x720)"
CAPTURE_FPS="$(cfg video.display.capture_framerate --default 30)"
PIP_PC="$(cfg video.display.picture_in_picture.width_percent --default 25)"
MARGIN="$(cfg video.display.picture_in_picture.margin_pixels --default 24)"
[[ "$CAPTURE_SIZE" =~ ^[0-9]+x[0-9]+$ && "$CAPTURE_FPS" =~ ^[0-9]+$ ]] || exit 12
CW="${CAPTURE_SIZE%x*}"; CH="${CAPTURE_SIZE#*x}"

KMS_INFO="$(kmsprint 2>/dev/null)" || exit 12
CONNECTOR_ID="$(awk '/^Connector [0-9]+ \([0-9]+\) HDMI-A-[0-9]+ \(connected\)/ {gsub(/[()]/, "", $3); print $3; exit}' <<<"$KMS_INFO")"
MODE="$(awk '/^[[:space:]]*Crtc [0-9]+ / {split($4, mode, "@"); print mode[1]; exit}' <<<"$KMS_INFO")"
[[ "$CONNECTOR_ID" =~ ^[0-9]+$ && "$MODE" =~ ^[0-9]+x[0-9]+$ ]] || exit 12
DW="${MODE%x*}"; DH="${MODE#*x}"
PW=$((DW * PIP_PC / 100)); PW=$((PW / 2 * 2)); PH=$((PW * 9 / 16)); PH=$((PH / 2 * 2))
PX=$((DW - PW - MARGIN)); PY=$((DH - PH - MARGIN))
(( DW > 0 && DH > 0 && PW > 0 && PH > 0 && PX >= 0 && PY >= 0 )) || exit 12

SOCKET_DIR="${XDG_RUNTIME_DIR:-/tmp}"
FRONT_SOCKET="${SOCKET_DIR}/hpr-video-$(id -u)-${MAIN}.sock"
REAR_SOCKET="${SOCKET_DIR}/hpr-video-$(id -u)-${PIP}.sock"
[[ "$FRONT_SOCKET" != "$REAR_SOCKET" ]] || exit 12
rm -f -- "$FRONT_SOCKET" "$REAR_SOCKET"
CAPTURE_PID=''; MAIN_PUBLISHER_PID=''; PIP_PUBLISHER_PID=''
cleanup() {
  local pid
  for pid in "$MAIN_PUBLISHER_PID" "$PIP_PUBLISHER_PID" "$CAPTURE_PID"; do
    [[ -z "$pid" ]] || kill "$pid" 2>/dev/null || true
  done
  for pid in "$MAIN_PUBLISHER_PID" "$PIP_PUBLISHER_PID" "$CAPTURE_PID"; do
    [[ -z "$pid" ]] || wait "$pid" 2>/dev/null || true
  done
  rm -f -- "$FRONT_SOCKET" "$REAR_SOCKET"
}
trap cleanup EXIT
trap 'exit 0' TERM INT

PIPELINE=(/usr/bin/gst-launch-1.0 -q -e compositor name=mix background=black force-live=true ignore-inactive-pads=true
  sink_0::xpos=0 sink_0::ypos=0 "sink_0::width=${DW}" "sink_0::height=${DH}" sink_0::sizing-policy=keep-aspect-ratio)
if [[ -n "$MD" && -n "$PD" ]]; then
  PIPELINE+=("sink_1::xpos=${PX}" "sink_1::ypos=${PY}" "sink_1::width=${PW}" "sink_1::height=${PH}" sink_1::sizing-policy=keep-aspect-ratio)
fi
PIPELINE+=('!' "video/x-raw,width=${DW},height=${DH},framerate=${CAPTURE_FPS}/1" '!' videoconvert '!' kmssink
  driver-name=vc4 "connector-id=${CONNECTOR_ID}" sync=false force-modesetting=false enable-last-sample=false)

add_camera() {
  local name="$1" device="$2" socket="$3" sink="$4" direction
  direction="$(orientation "$(cam "$name" rotation none)")"
  PIPELINE+=(v4l2src "device=${device}" io-mode=mmap do-timestamp=true '!' "image/jpeg,width=${CW},height=${CH},framerate=${CAPTURE_FPS}/1"
    '!' tee "name=${name}tee" "${name}tee." '!' queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream
    '!' v4l2jpegdec '!' videoconvert '!' videoflip "video-direction=${direction}"
    '!' queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream '!' "mix.${sink}"
    "${name}tee." '!' queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream
    '!' shmsink "socket-path=${socket}" shm-size=8388608 wait-for-connection=false sync=false enable-last-sample=false)
}
if [[ -n "$MD" ]]; then add_camera "$MAIN" "$MD" "$FRONT_SOCKET" sink_0; fi
if [[ -n "$PD" ]]; then
  if [[ -n "$MD" ]]; then add_camera "$PIP" "$PD" "$REAR_SOCKET" sink_1
  else add_camera "$PIP" "$PD" "$REAR_SOCKET" sink_0; fi
fi

echo "Low-latency HDMI: MJPEG ${CAPTURE_SIZE}@${CAPTURE_FPS}, KMS ${MODE}, PiP ${PW}x${PH}; streams isolated by leaky MJPEG queues." >&2
"${PIPELINE[@]}" & CAPTURE_PID=$!

publish() {
  local name="$1" socket="$2" stream_size sw sh fps bitrate gop direction path
  stream_size="$(cam "$name" stream_size 640x360)"; sw="${stream_size%x*}"; sh="${stream_size#*x}"
  fps="$(cam "$name" output_framerate 25)"; bitrate="$(bitrate_kbps "$(cam "$name" bitrate 700k)")"
  gop="$(cam "$name" gop 25)"; path="$(cam "$name" path "$name")"
  direction="$(orientation "$(cam "$name" rotation none)")"
  [[ "$stream_size" =~ ^[0-9]+x[0-9]+$ && "$fps" =~ ^[0-9]+$ && "$bitrate" =~ ^[0-9]+$ && "$gop" =~ ^[0-9]+$ ]] || return 1
  /usr/bin/nice -n 5 /usr/bin/gst-launch-1.0 -q -e shmsrc "socket-path=${socket}" is-live=true do-timestamp=true
    '!' "image/jpeg,width=${CW},height=${CH},framerate=${CAPTURE_FPS}/1" '!' jpegdec '!' videoflip "video-direction=${direction}"
    '!' videoconvert '!' videoscale '!' videorate drop-only=true
    '!' "video/x-raw,width=${sw},height=${sh},framerate=${fps}/1,format=I420"
    '!' x264enc speed-preset=ultrafast tune=zerolatency "bitrate=${bitrate}" "key-int-max=${gop}" bframes=0
    '!' h264parse config-interval=1 '!' rtspclientsink "location=rtsp://127.0.0.1:8554/${path}" protocols=tcp
}

for _ in 1 2 3 4 5; do
  [[ -z "$MD" || -S "$FRONT_SOCKET" ]] && [[ -z "$PD" || -S "$REAR_SOCKET" ]] && break
  kill -0 "$CAPTURE_PID" 2>/dev/null || exit 1
  sleep 1
done
[[ -z "$MD" || -S "$FRONT_SOCKET" ]] && [[ -z "$PD" || -S "$REAR_SOCKET" ]] || exit 1
if [[ -n "$MD" ]]; then publish "$MAIN" "$FRONT_SOCKET" & MAIN_PUBLISHER_PID=$!; fi
if [[ -n "$PD" ]]; then publish "$PIP" "$REAR_SOCKET" & PIP_PUBLISHER_PID=$!; fi

hardware_state() {
  find /dev/v4l/by-path -maxdepth 1 -type l -name '*usbv2*video-index0' -printf '%l\n' 2>/dev/null | sort
  grep -h . /sys/class/drm/card*-HDMI-A-*/status /sys/class/drm/card*-HDMI-A-*/modes 2>/dev/null || true
}
INITIAL_HARDWARE="$(hardware_state)"
while sleep 2; do
  [[ "$(hardware_state)" == "$INITIAL_HARDWARE" ]] || { echo 'Camera or HDMI state changed; rebuilding video mode.' >&2; exit 75; }
  kill -0 "$CAPTURE_PID" 2>/dev/null || { echo 'KMS capture/display pipeline stopped; restoring FFmpeg fallback.' >&2; exit 1; }
  if [[ -n "$MD" ]] && ! kill -0 "$MAIN_PUBLISHER_PID" 2>/dev/null; then
    wait "$MAIN_PUBLISHER_PID" 2>/dev/null || true
    publish "$MAIN" "$FRONT_SOCKET" & MAIN_PUBLISHER_PID=$!
  fi
  if [[ -n "$PD" ]] && ! kill -0 "$PIP_PUBLISHER_PID" 2>/dev/null; then
    wait "$PIP_PUBLISHER_PID" 2>/dev/null || true
    publish "$PIP" "$REAR_SOCKET" & PIP_PUBLISHER_PID=$!
  fi
done

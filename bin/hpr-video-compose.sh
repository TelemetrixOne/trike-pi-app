#!/usr/bin/env bash
set -euo pipefail
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
cfg() { PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value --config "${CONFIG_PATH}" "$@"; }
cam() { cfg "video.cameras.${1}.${2}" --default "${3}"; }
flip() {
  case "$1" in
    none) printf identity;; clockwise_90) printf 'glvideoflip method=clockwise';;
    180) printf 'glvideoflip method=rotate-180';;
    anticlockwise_90) printf 'glvideoflip method=counterclockwise';;
    *) echo "Unsupported camera rotation: $1" >&2; return 1;;
  esac
}
soft_flip() {
  case "$1" in
    none) printf identity;; clockwise_90) printf 'videoflip method=clockwise';;
    180) printf 'videoflip method=rotate-180';;
    anticlockwise_90) printf 'videoflip method=counterclockwise';;
  esac
}
bitrate() { case "$1" in *k) echo "$((${1%k}*1000))";; *M) echo "$((${1%M}*1000000))";; *) echo "$1";; esac; }

MAIN="$(cfg video.display.camera --default front)"; PIP="$(cfg video.display.picture_in_picture.camera --default rear)"
SIZE="$(cfg video.display.capture_size --default 1920x1080)"; FPS="$(cfg video.display.capture_framerate --default 30)"
PIP_PC="$(cfg video.display.picture_in_picture.width_percent --default 25)"; MARGIN="$(cfg video.display.picture_in_picture.margin_pixels --default 24)"
MD="$(cam "$MAIN" device '')"; PD="$(cam "$PIP" device '')"; MP="$(cam "$MAIN" path "$MAIN")"; PP="$(cam "$PIP" path "$PIP")"
MF="$(cam "$MAIN" output_framerate 25)"; PF="$(cam "$PIP" output_framerate 25)"; MB="$(cam "$MAIN" bitrate 700k)"; PB="$(cam "$PIP" bitrate 700k)"
MR="$(cam "$MAIN" rotation none)"; PR="$(cam "$PIP" rotation none)"; MGF="$(flip "$MR")"; PGF="$(flip "$PR")"; MSF="$(soft_flip "$MR")"; PSF="$(soft_flip "$PR")"
CW="${SIZE%x*}"; CH="${SIZE#*x}"; DS="$(tr ',' 'x' </sys/class/graphics/fb0/virtual_size)"
[[ "$DS" =~ ^[0-9]+x[0-9]+$ ]] || { echo 'Cannot determine native display size' >&2; exit 12; }
DW="${DS%x*}"; DH="${DS#*x}"; PW=$((DW*PIP_PC/100)); PH=$((PW*CH/CW)); PX=$((DW-PW-MARGIN)); PY=$((DH-PH-MARGIN))
[[ -n "$MD" && -n "$PD" && "$MD" != "$PD" ]] || { echo 'Cameras need distinct stable device paths' >&2; exit 10; }
for d in "$MD" "$PD"; do while [[ ! -e "$d" ]]; do echo "Waiting for $d" >&2; sleep 5; done; done

# Calculate an aspect-preserving crop in source orientation. This removes edges
# before GPU rotation, fills the native panel, and never stretches the picture.
MCROP=''
if [[ "$MR" == clockwise_90 || "$MR" == anticlockwise_90 ]]; then
  cropw=$((CH*DH/DW)); cropw=$((cropw/2*2)); left=$(((CW-cropw)/2)); right=$((CW-cropw-left))
  (( cropw < CW )) && MCROP="videocrop left=$left right=$right ! "
else
  croph=$((CW*DH/DW)); croph=$((croph/2*2)); top=$(((CH-croph)/2)); bottom=$((CH-croph-top))
  (( croph < CH )) && MCROP="videocrop top=$top bottom=$bottom ! "
fi
for e in v4l2src jpegparse jpegdec glupload glvideoflip glvideomixer glimagesink v4l2h264enc rtspclientsink; do
  gst-inspect-1.0 "$e" >/dev/null 2>&1 || { echo "Missing GStreamer element: $e" >&2; exit 13; }
done
export GST_GL_PLATFORM=egl GST_GL_WINDOW=gbm
echo "GPU compositor: ${SIZE}@${FPS} to native ${DS}; aspect crop; PiP ${PW}x${PH}." >&2
exec gst-launch-1.0 -e \
  glvideomixer name=mix background=black sink_0::xpos=0 sink_0::ypos=0 sink_0::width="$DW" sink_0::height="$DH" sink_1::xpos="$PX" sink_1::ypos="$PY" sink_1::width="$PW" sink_1::height="$PH" \
    ! "video/x-raw(memory:GLMemory),width=$DW,height=$DH,framerate=$FPS/1" ! glimagesink sync=false qos=false force-aspect-ratio=false \
  v4l2src device="$MD" io-mode=mmap do-timestamp=true ! "image/jpeg,width=$CW,height=$CH,framerate=$FPS/1" ! jpegparse ! jpegdec idct-method=ifast ! tee name=main \
  main. ! queue leaky=downstream max-size-buffers=2 ! $MCROP glupload ! glcolorconvert ! $MGF ! mix.sink_0 \
  main. ! queue leaky=downstream max-size-buffers=2 ! videoconvert ! $MSF ! videoscale ! videorate ! "video/x-raw,width=640,height=480,framerate=$MF/1,format=I420" ! v4l2h264enc extra-controls="controls,video_bitrate=$(bitrate "$MB"),repeat_sequence_header=1" ! h264parse config-interval=-1 ! rtspclientsink location="rtsp://127.0.0.1:8554/$MP" protocols=tcp latency=0 \
  v4l2src device="$PD" io-mode=mmap do-timestamp=true ! "image/jpeg,width=$CW,height=$CH,framerate=$FPS/1" ! jpegparse ! jpegdec idct-method=ifast ! tee name=pip \
  pip. ! queue leaky=downstream max-size-buffers=2 ! glupload ! glcolorconvert ! $PGF ! mix.sink_1 \
  pip. ! queue leaky=downstream max-size-buffers=2 ! videoconvert ! $PSF ! videoscale ! videorate ! "video/x-raw,width=640,height=480,framerate=$PF/1,format=I420" ! v4l2h264enc extra-controls="controls,video_bitrate=$(bitrate "$PB"),repeat_sequence_header=1" ! h264parse config-interval=-1 ! rtspclientsink location="rtsp://127.0.0.1:8554/$PP" protocols=tcp latency=0

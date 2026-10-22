#!/usr/bin/env bash
set -euo pipefail
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
TIMESTAMP_ENV="$(PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" \
  -m hpr_gateway.video_timestamp --config "$CONFIG_PATH")"
eval "$TIMESTAMP_ENV"
# Read central identities/settings, then the approved Pi-local quality file.
# OS Python provides the packaged GStreamer introspection bindings.
HPR_VIDEO_SETTINGS="$(PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" \
  -m hpr_gateway.video_local --config "$CONFIG_PATH" \
  --local-config "${HPR_VIDEO_LOCAL_PATH:-/etc/hpr/video-local.yaml}")"
export HPR_VIDEO_SETTINGS
exec /usr/bin/python3 "${INSTALL_ROOT}/runtime/video/hpr-video-native.py"

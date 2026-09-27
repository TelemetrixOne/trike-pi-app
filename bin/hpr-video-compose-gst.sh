#!/usr/bin/env bash
set -euo pipefail
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
# Read through the supported central-config loader using the gateway venv.
# OS Python provides the packaged GStreamer introspection bindings.
HPR_VIDEO_SETTINGS="$(PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" -c \
  'import json,sys; from hpr_gateway.config import load_config; print(json.dumps(load_config(sys.argv[1]).get("video", {})))' "$CONFIG_PATH")"
export HPR_VIDEO_SETTINGS
exec /usr/bin/python3 "${INSTALL_ROOT}/runtime/video/hpr-video-native.py"

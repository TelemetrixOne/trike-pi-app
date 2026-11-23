#!/usr/bin/env bash
set -euo pipefail
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
# Historical entry-point name retained for installed stream supervisor units.
exec "${INSTALL_ROOT}/runtime/video/hpr-video-compose-gst.sh"

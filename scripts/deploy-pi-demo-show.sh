#!/usr/bin/env bash
# Scoped, reversible deployment of the camera-only HDMI path and demo listener.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/opt/hpr/backups/demo-show-${STAMP}"

[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
for source in bin/hpr-video-native.py hpr_gateway/services/demo_show.py; do
  [[ -f "${REPO_ROOT}/${source}" ]] || { echo "Missing ${source}" >&2; exit 1; }
done
"/usr/bin/python3" -m py_compile "${REPO_ROOT}/bin/hpr-video-native.py"
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "${REPO_ROOT}/hpr_gateway/services/demo_show.py"
gst-inspect-1.0 kmssink >/dev/null
PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config "$CONFIG_PATH"
cfg() {
  PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.config_value \
    --config "$CONFIG_PATH" "$@"
}
SERVICE_USER="$(cfg system.service_user --default admin)"
SERVICE_GROUP="$(cfg system.service_group --default "${SERVICE_USER}")"
id "${SERVICE_USER}" >/dev/null
getent group "${SERVICE_GROUP}" >/dev/null
[[ -f /etc/systemd/system/hpr-video-console.service ]] || { echo 'Missing HDMI console unit' >&2; exit 1; }
[[ -x /usr/bin/ffmpeg ]] || { echo 'Missing ffmpeg' >&2; exit 1; }

install -d -m 0700 "$BACKUP_ROOT"
for target in \
  "${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh" \
  "${INSTALL_ROOT}/runtime/video/hpr-video-native.py" \
  "${INSTALL_ROOT}/runtime/video/startup-splash.png" \
  "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py" \
  "${INSTALL_ROOT}/hpr_gateway/services/demo_show.py" \
  /etc/systemd/system/hpr-video-splash.service \
  /etc/systemd/system/hpr-startup-show.service \
  /etc/systemd/system/hpr-demo-show.service \
  /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf; do
  if [[ -e "$target" ]]; then
    install -d -m 0700 "$(dirname "${BACKUP_ROOT}${target}")"
    cp -a "$target" "${BACKUP_ROOT}${target}"
  fi
done

NATIVE_CHANGED=false
if ! cmp -s "${REPO_ROOT}/bin/hpr-video-native.py" "${INSTALL_ROOT}/runtime/video/hpr-video-native.py"; then
  NATIVE_CHANGED=true
  touch "${BACKUP_ROOT}/.native-updated"
fi
install -m 0755 "${REPO_ROOT}/bin/hpr-video-native.py" "${INSTALL_ROOT}/runtime/video/hpr-video-native.py"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/demo_show.py" "${INSTALL_ROOT}/hpr_gateway/services/demo_show.py"
systemctl disable --now hpr-video-splash.service 2>/dev/null || true
rm -f -- \
  "${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh" \
  "${INSTALL_ROOT}/runtime/video/startup-splash.png" \
  /etc/systemd/system/hpr-video-splash.service \
  /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf \
  /run/hpr-demo-show/hdmi-splash.json

systemctl disable --now hpr-startup-show.service 2>/dev/null || true
if systemctl is-active --quiet hpr-startup-show.service; then
  echo 'Refusing to replace an active automatic startup show' >&2
  exit 1
fi
rm -f /etc/systemd/system/hpr-startup-show.service
rm -f "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py"

cat > /etc/systemd/system/hpr-demo-show.service <<EOF
[Unit]
Description=HPR operator-triggered light, horn and DRS demo
After=hpr-gpio-control.service
Wants=hpr-gpio-control.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_ROOT}
Environment=PYTHONPATH=${INSTALL_ROOT}
ExecStart=${INSTALL_ROOT}/venv/bin/python -m hpr_gateway.services.demo_show --config ${CONFIG_PATH}
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/hpr-demo-show.service
systemctl enable hpr-demo-show.service
if [[ "$NATIVE_CHANGED" == true ]]; then
  systemctl restart hpr-video-compositor.service
  systemctl is-active --quiet hpr-video-compositor.service
fi
systemctl restart hpr-demo-show.service
systemctl is-active --quiet hpr-demo-show.service
echo "HDMI splash removed; camera-only video and on-demand demo listener updated. GPIO was not restarted. Backup: ${BACKUP_ROOT}"

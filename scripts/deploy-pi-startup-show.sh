#!/usr/bin/env bash
# Scoped, reversible deployment. Does not restart any running service.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="/opt/hpr/backups/startup-show-${STAMP}"

[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
for source in bin/hpr-video-splash.sh assets/startup-splash.png hpr_gateway/services/startup_show.py; do
  [[ -f "${REPO_ROOT}/${source}" ]] || { echo "Missing ${source}" >&2; exit 1; }
done
bash -n "${REPO_ROOT}/bin/hpr-video-splash.sh"
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "${REPO_ROOT}/hpr_gateway/services/startup_show.py"
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
  "${INSTALL_ROOT}/runtime/video/startup-splash.png" \
  "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py" \
  /etc/systemd/system/hpr-video-splash.service \
  /etc/systemd/system/hpr-startup-show.service \
  /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf; do
  if [[ -e "$target" ]]; then
    install -d -m 0700 "$(dirname "${BACKUP_ROOT}${target}")"
    cp -a "$target" "${BACKUP_ROOT}${target}"
  fi
done

install -m 0755 "${REPO_ROOT}/bin/hpr-video-splash.sh" "${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh"
install -m 0644 "${REPO_ROOT}/assets/startup-splash.png" "${INSTALL_ROOT}/runtime/video/startup-splash.png"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/startup_show.py" "${INSTALL_ROOT}/hpr_gateway/services/startup_show.py"

cat > /etc/systemd/system/hpr-video-splash.service <<EOF
[Unit]
Description=HPR early HDMI startup image
After=local-fs.target
Before=hpr-video-console.service

[Service]
Type=oneshot
Environment=HPR_INSTALL_ROOT=${INSTALL_ROOT}
ExecStart=${INSTALL_ROOT}/runtime/video/hpr-video-splash.sh
TimeoutStartSec=6
RemainAfterExit=yes
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

install -d -m 0755 /etc/systemd/system/hpr-video-console.service.d
cat > /etc/systemd/system/hpr-video-console.service.d/20-startup-splash.conf <<'EOF'
[Unit]
Wants=hpr-video-splash.service
After=hpr-video-splash.service
EOF

cat > /etc/systemd/system/hpr-startup-show.service <<EOF
[Unit]
Description=HPR one-time boot light and horn flourish
After=hpr-gpio-control.service
Wants=hpr-gpio-control.service

[Service]
Type=oneshot
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_ROOT}
Environment=PYTHONPATH=${INSTALL_ROOT}
ExecStart=${INSTALL_ROOT}/venv/bin/python -m hpr_gateway.services.startup_show --config ${CONFIG_PATH}
TimeoutStartSec=25
RuntimeDirectory=hpr-startup
RuntimeDirectoryPreserve=yes
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/hpr-video-splash.service /etc/systemd/system/hpr-startup-show.service
systemctl enable hpr-startup-show.service
echo "Startup sequence installed for next boot; running services were not restarted. Backup: ${BACKUP_ROOT}"

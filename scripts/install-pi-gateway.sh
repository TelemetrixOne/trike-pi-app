#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_SCOPE="${HPR_INSTALL_SCOPE:-all}"

read_yaml_scalar() {
  local key="$1"
  local default_value="$2"
  "${PYTHON_BIN}" - "${CONFIG_PATH}" "${key}" "${default_value}" <<'PY'
import sys

path, dotted, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    lines = open(path, "r", encoding="utf-8").read().splitlines()
except FileNotFoundError:
    print(default)
    raise SystemExit(0)

node = {}
stack = [(-1, node)]
for raw in lines:
    line = raw.split("#", 1)[0].rstrip()
    if not line.strip() or ":" not in line:
        continue
    indent = len(line) - len(line.lstrip(" "))
    key, value = line.strip().split(":", 1)
    while stack and indent <= stack[-1][0]:
        stack.pop()
    parent = stack[-1][1]
    value = value.strip()
    if not value:
        child = {}
        parent[key] = child
        stack.append((indent, child))
        continue
    if value in {"null", "Null", "NULL"}:
        parent[key] = ""
    elif value in {"true", "True", "TRUE"}:
        parent[key] = "true"
    elif value in {"false", "False", "FALSE"}:
        parent[key] = "false"
    else:
        parent[key] = value.strip('"').strip("'")

current = node
for part in dotted.split("."):
    if not isinstance(current, dict) or part not in current:
        print(default)
        break
    current = current[part]
else:
    print(current)
PY
}

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root, for example: sudo scripts/install-pi-gateway.sh" >&2
  exit 1
fi

case "${INSTALL_SCOPE}" in
  all|gps)
    ;;
  *)
    echo "HPR_INSTALL_SCOPE must be 'all' or 'gps' (received '${INSTALL_SCOPE}')." >&2
    exit 1
    ;;
esac

if [ ! -f "${CONFIG_PATH}" ]; then
  mkdir -p "$(dirname "${CONFIG_PATH}")"
  cp "${REPO_ROOT}/config/hpr.example.yaml" "${CONFIG_PATH}"
  echo "Created ${CONFIG_PATH}. Edit it before starting services." >&2
  exit 1
fi

for required_key in \
  hpr.trike_id network.mqtt.host network.mqtt.username network.mqtt.password \
  network.home_assistant.host
do
  required_value="$(read_yaml_scalar "${required_key}" '')"
  if [ -z "${required_value}" ] || [ "${required_value}" = "CHANGE_ME" ]; then
    echo "Config value must be set before installation: ${required_key}" >&2
    exit 1
  fi
done

INSTALL_ROOT="${HPR_INSTALL_ROOT:-$(read_yaml_scalar system.install_root /opt/hpr/gateway)}"
VENV_DIR="${INSTALL_ROOT}/venv"

if ! command -v rsync >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "rsync is required but apt-get is unavailable." >&2
    exit 1
  fi
  apt-get update
  apt-get install -y rsync
fi

install -d "${INSTALL_ROOT}"
rsync -a --delete \
  --exclude '/venv/' \
  --exclude '/runtime/' \
  "${REPO_ROOT}/pi-gateway/" "${INSTALL_ROOT}/"

ensure_python_venv() {
  if "${PYTHON_BIN}" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Python venv support is required but apt-get is unavailable." >&2
    return 1
  fi

  local python_version versioned_package
  python_version="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  versioned_package="python${python_version}-venv"

  echo "Installing Python venv support (${versioned_package})..."
  apt-get update
  if ! apt-get install -y "${versioned_package}"; then
    echo "${versioned_package} was unavailable; trying python3-venv." >&2
    apt-get install -y python3-venv
  fi
}

ensure_python_venv

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y rsync mosquitto-clients
  if [ "${INSTALL_SCOPE}" = "all" ]; then
    apt-get install -y \
      bluez bluetooth rfkill ffmpeg v4l-utils i2c-tools python3-smbus \
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-gl \
      gstreamer1.0-rtsp \
      build-essential python3-dev swig
  fi
fi

if [ "${INSTALL_SCOPE}" = "all" ] && \
   { [ "$(read_yaml_scalar services.gpio_control.enabled false)" = "true" ] || \
     [ "$(read_yaml_scalar services.gpio_config_sync.enabled false)" = "true" ]; } && \
   command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3-lgpio
fi

if [ "$(read_yaml_scalar services.gps.enabled false)" = "true" ] && \
   [ "$(read_yaml_scalar gps.gpsd_enabled true)" = "true" ] && \
   ! "${PYTHON_BIN}" -c 'import gps' >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "The system python3-gps binding is required but apt-get is unavailable." >&2
    exit 1
  fi
  apt-get update
  apt-get install -y python3-gps
fi

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip
REQUIREMENTS_FILE="${INSTALL_ROOT}/requirements.txt"
if [ "${INSTALL_SCOPE}" = "gps" ]; then
  REQUIREMENTS_FILE="${INSTALL_ROOT}/requirements.gps.txt"
fi
"${VENV_DIR}/bin/pip" install --prefer-binary -r "${REQUIREMENTS_FILE}"

PYTHONPATH="${INSTALL_ROOT}" "${VENV_DIR}/bin/python" -m hpr_gateway.validate_config --config "${CONFIG_PATH}"

cfg() {
  PYTHONPATH="${INSTALL_ROOT}" "${VENV_DIR}/bin/python" -m hpr_gateway.config_value --config "${CONFIG_PATH}" "$@"
}

SERVICE_USER="$(cfg system.service_user --default admin)"
SERVICE_GROUP="$(cfg system.service_group --default "${SERVICE_USER}")"
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "Configured service user does not exist: ${SERVICE_USER}" >&2
  exit 1
fi
if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
  echo "Configured service group does not exist: ${SERVICE_GROUP}" >&2
  exit 1
fi
for supplementary_group in bluetooth dialout gpio i2c video; do
  if getent group "${supplementary_group}" >/dev/null 2>&1; then
    usermod -aG "${supplementary_group}" "${SERVICE_USER}"
  fi
done
LOG_DIR="$(cfg system.log_dir --default /var/log/hpr)"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${LOG_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${LOG_DIR}"
TAILSCALE_ENABLED="$(cfg tailscale.enabled --default false)"
TAILSCALE_REQUIRED="$(cfg network.tailscale_required --default false)"
if [ "${TAILSCALE_ENABLED}" != "true" ]; then
  TAILSCALE_REQUIRED="false"
fi
PI_HOSTNAME="$(cfg pi.hostname --expand)"

if [ "${INSTALL_SCOPE}" = "all" ] && [ "$(cfg pi.set_system_hostname --default false)" = "true" ]; then
  hostnamectl set-hostname "${PI_HOSTNAME}"
  if grep -qE '^127\.0\.1\.1[[:space:]]+' /etc/hosts; then
    sed -i -E "s/^127\.0\.1\.1[[:space:]]+.*/127.0.1.1\t${PI_HOSTNAME}/" /etc/hosts
  else
    printf '127.0.1.1\t%s\n' "${PI_HOSTNAME}" >> /etc/hosts
  fi
  if [ -d /etc/cloud/cloud.cfg.d ]; then
    cat > /etc/cloud/cloud.cfg.d/99-hpr-hostname.cfg <<'EOF'
preserve_hostname: true
EOF
  fi
  for cloud_user_data in /boot/firmware/user-data /boot/user-data; do
    if [ -f "${cloud_user_data}" ] && grep -qE '^[[:space:]]*hostname:' "${cloud_user_data}"; then
      sed -i -E "s/^[[:space:]]*hostname:[[:space:]].*/hostname: ${PI_HOSTNAME}/" "${cloud_user_data}"
    fi
  done
fi

install_tailscale_if_missing() {
  if command -v tailscale >/dev/null 2>&1; then
    return 0
  fi
  if [ "$(cfg tailscale.install_if_missing --default false)" != "true" ]; then
    echo "Tailscale is enabled but tailscale CLI is not installed." >&2
    return 1
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y tailscale && return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
    return 0
  fi
  echo "Cannot install Tailscale: neither apt-get nor curl is available." >&2
  return 1
}

configure_tailscale() {
  if [ "$(cfg tailscale.enabled --default false)" != "true" ]; then
    return 0
  fi

  install_tailscale_if_missing
  systemctl enable --now tailscaled.service

  local auth_env auth_file auth_key hostname tags accept_routes accept_dns
  auth_env="$(cfg tailscale.auth_key_env --default HPR_TAILSCALE_AUTHKEY)"
  auth_file="$(cfg tailscale.auth_key_file --default "")"
  auth_key="${!auth_env:-}"
  hostname="$(cfg tailscale.hostname --default "${PI_HOSTNAME}" --expand)"
  tags="$(cfg tailscale.advertise_tags --default "")"
  accept_routes="$(cfg tailscale.accept_routes --default false)"
  accept_dns="$(cfg tailscale.accept_dns --default true)"

  if [ -z "${auth_key}" ] && [ -n "${auth_file}" ] && [ -r "${auth_file}" ]; then
    auth_key="$(tr -d '\r\n' < "${auth_file}")"
  fi

  if [ -z "${auth_key}" ] && tailscale status >/dev/null 2>&1; then
    echo "Tailscale is already registered; preserving the existing node settings."
    return 0
  fi

  if [ -z "${auth_key}" ] && ! tailscale status >/dev/null 2>&1 && [ -t 0 ]; then
    printf 'Enter Tailscale auth key for %s: ' "${hostname}" >&2
    read -r -s auth_key
    printf '\n' >&2
  fi

  local args
  args=(up "--hostname=${hostname}")
  if [ "${accept_routes}" = "true" ]; then
    args+=("--accept-routes")
  fi
  if [ "${accept_dns}" != "true" ]; then
    args+=("--accept-dns=false")
  fi
  if [ -n "${tags}" ]; then
    args+=("--advertise-tags=${tags}")
  fi
  if [ -n "${auth_key}" ]; then
    args+=("--authkey=${auth_key}")
  elif ! tailscale status >/dev/null 2>&1; then
    echo "Tailscale is not registered. Re-run with ${auth_env}=<auth-key> or set tailscale.auth_key_file." >&2
    return 1
  fi

  tailscale "${args[@]}"
}

if [ "${INSTALL_SCOPE}" = "all" ]; then
  configure_tailscale
fi

configure_gpsd() {
  if [ "$(cfg services.gps.enabled --default false)" != "true" ] || \
     [ "$(cfg gps.gpsd_enabled --default true)" != "true" ]; then
    return 0
  fi

  if ! command -v gpsd >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "gpsd is required but apt-get is unavailable." >&2
      return 1
    fi
    apt-get update
    apt-get install -y gpsd gpsd-clients
  fi

  local gps_device vendor_id product_id
  gps_device="$(cfg gps.device --default auto)"
  vendor_id="$(cfg gps.usb_vendor_id --default 1546)"
  product_id="$(cfg gps.usb_product_id --default 01a9)"
  vendor_id="${vendor_id#0x}"
  product_id="${product_id#0x}"
  if [ "${gps_device}" = "auto" ]; then
    gps_device=""
  fi

  cat > /etc/default/gpsd <<EOF
START_DAEMON="true"
USBAUTO="true"
DEVICES="${gps_device}"
GPSD_OPTIONS="-n"
EOF

  if [ "$(cfg gps.hotplug_recovery_enabled --default true)" = "true" ]; then
    cat > /etc/systemd/system/hpr-gps-hotplug.service <<EOF
[Unit]
Description=Recover gpsd after HPR GPS USB reconnect

[Service]
Type=oneshot
Environment=GPS_DEVICE=${gps_device}
ExecStart=/bin/sh -ec 'i=0; while [ ! -e "\$GPS_DEVICE" ] && [ \$i -lt 30 ]; do i=\$((i + 1)); sleep 0.5; done; test -e "\$GPS_DEVICE"; systemctl restart gpsd.service; systemctl restart hpr-gps.service'
EOF

    cat > /etc/udev/rules.d/99-hpr-gps-hotplug.rules <<EOF
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="${vendor_id}", ATTRS{idProduct}=="${product_id}", TAG+="systemd", ENV{SYSTEMD_WANTS}+="hpr-gps-hotplug.service"
EOF
  else
    rm -f /etc/systemd/system/hpr-gps-hotplug.service
    rm -f /etc/udev/rules.d/99-hpr-gps-hotplug.rules
  fi

  systemctl daemon-reload
  udevadm control --reload-rules
  systemctl enable --now gpsd.socket
  systemctl restart gpsd.service
}

configure_gpsd

install_unit() {
  local unit_name="$1"
  local module="$2"
  local unit_type="simple"
  local unit_user="${SERVICE_USER}"
  local unit_group="${SERVICE_GROUP}"
  if [ "${module}" = "gpio_control" ]; then
    unit_type="notify"
  fi
  if [ "${module}" = "gpio_config_sync" ] || [ "${module}" = "tpms_config_sync" ] || [ "${module}" = "power_cadence_config_sync" ]; then
    unit_user="root"
    unit_group="root"
  fi
  local after="network-online.target"
  local wants="network-online.target"
  if [ "${TAILSCALE_REQUIRED}" = "true" ]; then
    after="${after} tailscaled.service"
    wants="${wants} tailscaled.service"
  fi
  if [ "${module}" = "gps" ]; then
    after="${after} gpsd.service gpsd.socket"
    wants="${wants} gpsd.service gpsd.socket"
  fi
  case "${module}" in
    heart_rate|tpms|power_cadence|derailleur)
      after="${after} bluetooth.service"
      wants="${wants} bluetooth.service"
      ;;
  esac
  local exec_start_pre=""
  if [ "${module}" = "heart_rate" ]; then
    exec_start_pre="ExecStartPre=+${VENV_DIR}/bin/python -m hpr_gateway.bluetooth_power --config ${CONFIG_PATH} --role heart_rate"
  fi
  cat > "/etc/systemd/system/${unit_name}" <<EOF
[Unit]
Description=HPR Pi Gateway ${module}
After=${after}
Wants=${wants}

[Service]
Type=${unit_type}
User=${unit_user}
Group=${unit_group}
TimeoutStartSec=30
WorkingDirectory=${INSTALL_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${INSTALL_ROOT}
${exec_start_pre}
ExecStart=${VENV_DIR}/bin/python -m hpr_gateway.services.${module} --config ${CONFIG_PATH}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

configure_video() {
  if [ "$(cfg services.video.enabled --default false)" != "true" ]; then
    return 0
  fi

  local arch binary_source video_root expected_sha actual_sha
  arch="$(cfg video.mediamtx_arch --default linux_arm64)"
  binary_source="${INSTALL_ROOT}/vendor/mediamtx/${arch}/mediamtx"
  video_root="${INSTALL_ROOT}/runtime/video"
  if [ ! -f "${binary_source}" ]; then
    echo "Packaged MediaMTX binary is missing for ${arch}: ${binary_source}" >&2
    return 1
  fi
  expected_sha="$(cfg video.mediamtx_sha256 --default '')"
  if [ -n "${expected_sha}" ]; then
    actual_sha="$(sha256sum "${binary_source}" | awk '{print toupper($1)}')"
    if [ "${actual_sha}" != "$(printf '%s' "${expected_sha}" | tr '[:lower:]' '[:upper:]')" ]; then
      echo "Packaged MediaMTX checksum mismatch: ${actual_sha}" >&2
      return 1
    fi
  fi
  install -d "${video_root}"
  install -m 0755 "${binary_source}" "${video_root}/mediamtx"
  install -m 0755 "${INSTALL_ROOT}/bin/hpr-video-publish.sh" "${video_root}/hpr-video-publish.sh"
  install -m 0755 "${INSTALL_ROOT}/bin/hpr-video-compose.sh" "${video_root}/hpr-video-compose.sh"

  PYTHONPATH="${INSTALL_ROOT}" "${VENV_DIR}/bin/python" - "${CONFIG_PATH}" "${video_root}/mediamtx.yml" <<'PY'
import sys, yaml
source, target = sys.argv[1], sys.argv[2]
with open(source, encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle) or {}
cameras = ((cfg.get("video") or {}).get("cameras") or {})
paths = {
    str(camera.get("path") or name): {"source": "publisher"}
    for name, camera in cameras.items()
    if isinstance(camera, dict) and camera.get("enabled")
}
payload = {
    "logLevel": "info",
    "logDestinations": ["stdout"],
    "rtsp": True,
    "rtspAddress": ":8554",
    "rtmp": False,
    "hls": False,
    "webrtc": False,
    "srt": False,
    "paths": paths,
}
with open(target, "w", encoding="utf-8") as handle:
    yaml.safe_dump(payload, handle, sort_keys=False)
PY

  cat > /etc/systemd/system/hpr-video-mediamtx.service <<EOF
[Unit]
Description=HPR on-trike MediaMTX RTSP server
After=local-fs.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${video_root}
ExecStart=${video_root}/mediamtx ${video_root}/mediamtx.yml
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

  local camera display_enabled display_camera camera_after camera_requires pip_enabled
  display_enabled="$(cfg video.display.enabled --default false)"
  display_camera="$(cfg video.display.camera --default front)"
  pip_enabled="$(cfg video.display.picture_in_picture.enabled --default false)"
  if [ "${display_enabled}" = "true" ]; then
    cat > /etc/systemd/system/hpr-video-console.service <<'EOF'
[Unit]
Description=Prepare HPR dedicated HDMI video console
After=local-fs.target

[Service]
Type=oneshot
Environment=TERM=linux
ExecStart=/bin/sh -c '/usr/bin/setterm --cursor off --blank 0 --powersave off < /dev/tty1 > /dev/tty1'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    systemctl disable --now getty@tty1.service 2>/dev/null || true
    systemctl mask getty@tty1.service
  else
    rm -f /etc/systemd/system/hpr-video-console.service
    systemctl unmask getty@tty1.service 2>/dev/null || true
  fi

  for camera in front rear; do
    if [ "$(cfg "video.cameras.${camera}.enabled" --default false)" != "true" ]; then
      rm -f "/etc/systemd/system/hpr-video-${camera}.service"
      continue
    fi
    camera_after="hpr-video-mediamtx.service"
    camera_requires="hpr-video-mediamtx.service"
    if [ "${display_enabled}" = "true" ] && [ "${camera}" = "${display_camera}" ]; then
      camera_after="${camera_after} hpr-video-console.service"
      camera_requires="${camera_requires} hpr-video-console.service"
    fi
    cat > "/etc/systemd/system/hpr-video-${camera}.service" <<EOF
[Unit]
Description=HPR ${camera} USB camera publisher
After=${camera_after}
Requires=${camera_requires}
StartLimitIntervalSec=0

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
SupplementaryGroups=video render
WorkingDirectory=${video_root}
Environment=HPR_CONFIG_PATH=${CONFIG_PATH}
Environment=HPR_INSTALL_ROOT=${INSTALL_ROOT}
ExecStart=${video_root}/hpr-video-publish.sh ${camera}
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
  done

  cat > /etc/systemd/system/hpr-video-compositor.service <<EOF
[Unit]
Description=HPR direct dual-camera HDMI compositor and publishers
After=hpr-video-mediamtx.service hpr-video-console.service
Requires=hpr-video-mediamtx.service hpr-video-console.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
SupplementaryGroups=video render
WorkingDirectory=${video_root}
Environment=HPR_CONFIG_PATH=${CONFIG_PATH}
Environment=HPR_INSTALL_ROOT=${INSTALL_ROOT}
ExecStart=${video_root}/hpr-video-compose.sh
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl disable --now hpr-video-display.service 2>/dev/null || true
  rm -f /etc/systemd/system/hpr-video-display.service
}

install_marker_units() {
  cat > /etc/systemd/system/hpr-boot-marker.service <<'EOF'
[Unit]
Description=HPR boot marker
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'mkdir -p /var/log/hpr && echo "$(date --iso-8601=seconds) boot_id=$(cat /proc/sys/kernel/random/boot_id) uptime=$(cat /proc/uptime)" >> /var/log/hpr/boot-marker.log'

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/hpr-shutdown-marker.service <<'EOF'
[Unit]
Description=HPR shutdown marker
DefaultDependencies=no
Before=shutdown.target reboot.target halt.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'mkdir -p /var/log/hpr && echo "$(date --iso-8601=seconds) clean_shutdown boot_id=$(cat /proc/sys/kernel/random/boot_id)" >> /var/log/hpr/shutdown-marker.log'
TimeoutStartSec=5

[Install]
WantedBy=shutdown.target reboot.target halt.target
EOF
}

if [ "${INSTALL_SCOPE}" = "all" ]; then
  install_unit hpr-heartbeat.service heartbeat
  install_unit hpr-pi-power-health.service pi_power_health
  install_unit hpr-services-health.service services_health
  install_unit hpr-tpms.service tpms
  install_unit hpr-tpms-config-sync.service tpms_config_sync
  install_unit hpr-heart-rate.service heart_rate
  install_unit hpr-power-cadence.service power_cadence
  install_unit hpr-power-cadence-config-sync.service power_cadence_config_sync
  install_unit hpr-gpio-control.service gpio_control
  install_unit hpr-gpio-config-sync.service gpio_config_sync
  install_unit hpr-derailleur.service derailleur
  install_unit hpr-power-watch.service power_watch
  install_unit hpr-trike-config-sync.service trike_config_sync
  configure_video
  install_marker_units
fi
install_unit hpr-gps.service gps

systemctl daemon-reload

enable_service_if_configured() {
  local config_key="$1"
  local unit="$2"
  if [ "$(cfg "services.${config_key}.enabled" --default false)" = "true" ]; then
    systemctl enable "${unit}"
    if [ "${HPR_START_SERVICES:-true}" = "true" ]; then
      systemctl restart "${unit}"
    fi
  else
    systemctl disable --now "${unit}" 2>/dev/null || true
  fi
}

if [ "${INSTALL_SCOPE}" = "all" ]; then
  enable_service_if_configured heartbeat hpr-heartbeat.service
  enable_service_if_configured pi_power_health hpr-pi-power-health.service
  enable_service_if_configured services_health hpr-services-health.service
  enable_service_if_configured tpms hpr-tpms.service
  enable_service_if_configured tpms_config_sync hpr-tpms-config-sync.service
  enable_service_if_configured heart_rate hpr-heart-rate.service
  enable_service_if_configured power_cadence hpr-power-cadence.service
  enable_service_if_configured power_cadence_config_sync hpr-power-cadence-config-sync.service
  enable_service_if_configured gpio_control hpr-gpio-control.service
  enable_service_if_configured gpio_config_sync hpr-gpio-config-sync.service
  enable_service_if_configured derailleur hpr-derailleur.service
  enable_service_if_configured power_watch hpr-power-watch.service
  systemctl enable hpr-trike-config-sync.service
  if [ "${HPR_START_SERVICES:-true}" = "true" ]; then
    systemctl restart hpr-trike-config-sync.service
  fi
  systemctl enable hpr-boot-marker.service hpr-shutdown-marker.service
  enable_service_if_configured video hpr-video-mediamtx.service
  if [ "$(cfg services.video.enabled --default false)" = "true" ]; then
    if [ "$(cfg video.display.enabled --default false)" = "true" ]; then
      systemctl enable hpr-video-console.service
      if [ "${HPR_START_SERVICES:-true}" = "true" ]; then
        systemctl restart hpr-video-console.service
      fi
    fi
    if [ "$(cfg video.display.picture_in_picture.enabled --default false)" = "true" ]; then
      systemctl disable --now hpr-video-front.service hpr-video-rear.service 2>/dev/null || true
      systemctl enable hpr-video-compositor.service
      if [ "${HPR_START_SERVICES:-true}" = "true" ]; then
        systemctl restart hpr-video-compositor.service
      fi
    else
      systemctl disable --now hpr-video-compositor.service 2>/dev/null || true
      for camera in front rear; do
        if [ "$(cfg "video.cameras.${camera}.enabled" --default false)" = "true" ]; then
          systemctl enable "hpr-video-${camera}.service"
          if [ "${HPR_START_SERVICES:-true}" = "true" ]; then
            systemctl restart "hpr-video-${camera}.service"
          fi
        fi
      done
    fi
  fi
fi
enable_service_if_configured gps hpr-gps.service

echo "Installed HPR Pi gateway files under ${INSTALL_ROOT} (scope: ${INSTALL_SCOPE})."
echo "Configured services were enabled (start=${HPR_START_SERVICES:-true})."

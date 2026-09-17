#!/usr/bin/env bash
set -euo pipefail

TARGET="${HPR_SOURCE_PI_SSH_TARGET:-}"
if [ -z "${TARGET}" ]; then
  echo "Set HPR_SOURCE_PI_SSH_TARGET to user@host before running this audit." >&2
  exit 1
fi
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_ROOT="${HPR_CAPTURE_ROOT:-captures}"
OUT_DIR="${OUT_ROOT}/live-source-pi-${STAMP}"
COMMAND_DIR="${OUT_DIR}/commands"
FILE_DIR="${OUT_DIR}/files"

mkdir -p "${COMMAND_DIR}" "${FILE_DIR}"

cat > "${OUT_DIR}/README.txt" <<EOF
HPR live source Pi read-only capture.

SSH target: ${TARGET}
Captured: $(date -Is)

This script runs read-only discovery commands and copies selected files for review.
It does not stop, restart, disable, delete, edit, install, or reboot anything.

WARNING: Captured files may still contain operational details or secrets, even
though obvious secret files and large/runtime folders are excluded where possible.
Treat this archive as confidential.
EOF

run_remote() {
  local name="$1"
  local command="$2"
  printf 'Collecting %s\n' "${name}"
  {
    printf '$ %s\n\n' "${command}"
    ssh "${TARGET}" "timeout 60 bash -lc $(printf '%q' "${command}")" || true
  } > "${COMMAND_DIR}/${name}.txt" 2>&1
}

run_remote "00_identity" 'hostname; whoami; pwd; date; uname -a; cat /etc/os-release'
run_remote "01_network" 'ip addr; echo "--- tailscale status ---"; tailscale status 2>&1 || true'
run_remote "02_services_active" 'systemctl list-units --type=service --all --no-pager --plain | grep -i -E "hpr|tpms|gps|gpio|bluetooth|tailscale|mosquitto|go2rtc|camera|ffmpeg" || true'
run_remote "03_services_enabled" 'systemctl list-unit-files --type=service --no-pager | grep -i -E "hpr|tpms|gps|gpio|bluetooth|tailscale|mosquitto|go2rtc|camera|ffmpeg" || true'
run_remote "04_processes" 'ps aux | grep -i -E "hpr|tpms|gps|gpio|ble|mqtt|camera|ffmpeg|go2rtc|webrtc" | grep -v grep || true'
run_remote "05_opt_hpr_find" 'find /opt/hpr -maxdepth 5 -type f -printf "%M %u %g %s %TY-%Tm-%Td %TH:%TM %p\n" 2>/dev/null || true'
run_remote "06_home_admin_find" 'find /home/admin -maxdepth 6 -type f 2>/dev/null | grep -i -E "hpr|tpms|gps|gpio|mqtt|ble|camera|go2rtc|ffmpeg|webrtc" || true'
run_remote "07_key_dirs" 'ls -la /opt/hpr 2>/dev/null || true; echo "--- /etc/systemd/system matches ---"; ls -la /etc/systemd/system 2>/dev/null | grep -i -E "hpr|tpms|gps|gpio|camera|go2rtc" || true; echo "--- NetworkManager conf.d ---"; ls -la /etc/NetworkManager/conf.d 2>/dev/null || true; echo "--- udev rules ---"; ls -la /etc/udev/rules.d 2>/dev/null || true; echo "--- /etc/hpr ---"; ls -la /etc/hpr 2>/dev/null || true'
run_remote "08_bluetooth" 'bluetoothctl show 2>&1 || true; echo "--- rfkill ---"; rfkill list 2>&1 || true; echo "--- bluetooth journal ---"; journalctl -u bluetooth -n 80 --no-pager 2>&1 || true'
run_remote "09_cron" 'crontab -l 2>/dev/null || true; echo "--- sudo crontab -l, non-interactive ---"; sudo -n crontab -l 2>/dev/null || true'
run_remote "10_gps" 'systemctl list-units --type=service --all --no-pager --plain | grep -i gps || true; systemctl list-unit-files --type=service --no-pager | grep -i gps || true; ps aux | grep -i gps | grep -v grep || true; ls -la /dev/serial* /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true'
run_remote "11_camera_video" 'systemctl list-units --type=service --all --no-pager --plain | grep -i -E "camera|go2rtc|ffmpeg|webrtc|video" || true; ps aux | grep -i -E "camera|go2rtc|ffmpeg|webrtc|video" | grep -v grep || true; find /opt /home/admin -maxdepth 6 -type f 2>/dev/null | grep -i -E "camera|go2rtc|ffmpeg|webrtc|video" || true'
run_remote "12_topic_endpoint_credential_grep" 'grep -RInE "mqtt_hpv|hPr01|hPr|password|username|MQTT_USERNAME|MQTT_PASSWORD|HA_USERNAME|HA_PASSWORD|broker username|broker password|100\.|192\.168\.|10\.|172\.|localhost|127\.0\.0\.1|MQTT_HOST|HA_HOST|HOME_ASSISTANT|NGINX_HOST|broker|host[[:space:]]*=|url[[:space:]]*=|http://|https://|hpr/|hpv/|topic|Ashton" /opt/hpr /home/admin /etc/systemd/system /etc/hpr 2>/dev/null | head -n 2000 || true'

printf 'Collecting systemd unit bodies\n'
awk '{print $1}' "${COMMAND_DIR}/02_services_active.txt" "${COMMAND_DIR}/03_services_enabled.txt" \
  | grep -E '^[A-Za-z0-9_.@-]+\.service$' \
  | sort -u > "${OUT_DIR}/unit-list.txt" || true

while IFS= read -r unit; do
  [ -n "${unit}" ] || continue
  run_remote "systemctl-cat-${unit}" "systemctl cat ${unit}"
done < "${OUT_DIR}/unit-list.txt"

printf 'Building selected file list\n'
ssh "${TARGET}" 'find /opt/hpr /home/admin/hpr /home/admin/_archive_tpms /etc/systemd/system /opt/go2rtc -maxdepth 6 -type f 2>/dev/null |
  grep -i -E "hpr|tpms|gps|gpio|mqtt|ble|camera|go2rtc|ffmpeg|webrtc|service|yaml|py|sh" |
  grep -v -E "/(__pycache__|\.venv|venv|site-packages|logs)/|\.pyc$|\.db$|\.key$|\.pem$|secrets\.yaml|passwd$" || true' \
  > "${OUT_DIR}/selected-files.txt"

while IFS= read -r remote_path; do
  [ -n "${remote_path}" ] || continue
  local_name="$(printf '%s' "${remote_path#/}" | tr '/' '_')"
  scp -q "${TARGET}:${remote_path}" "${FILE_DIR}/${local_name}" || true
done < "${OUT_DIR}/selected-files.txt"

if command -v perl >/dev/null 2>&1; then
  find "${FILE_DIR}" -type f -print0 | xargs -0 perl -0pi -e 's/^(\s*(?:PASS|MQTT_PASS|MQTT_PASSWORD|HPR_MQTT_PASSWORD|PASSWORD)\s*=\s*["'\'']).*?(["'\'']\s*)$/${1}<REDACTED>${2}/gmi; s/(password\s*[:=]\s*)["'\'']?[^"'\''\s#]+/${1}<REDACTED>/gmi; s/(token\s*[:=]\s*)["'\'']?[^"'\''\s#]+/${1}<REDACTED>/gmi'
fi

tar -czf "${OUT_DIR}.tar.gz" -C "${OUT_ROOT}" "$(basename "${OUT_DIR}")"

printf '\nCapture complete:\n'
printf '  Folder: %s\n' "${OUT_DIR}"
printf '  Archive: %s.tar.gz\n' "${OUT_DIR}"
printf '\nWARNING: review captured files for secrets before sharing.\n'

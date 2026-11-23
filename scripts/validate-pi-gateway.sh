#!/usr/bin/env bash
set -uo pipefail

CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
REQUIRE_ACTIVE="${HPR_REQUIRE_ACTIVE:-true}"
FAILURES=0

pass() { printf 'PASS %-24s %s\n' "$1" "${2:-}"; }
warn() { printf 'WARN %-24s %s\n' "$1" "${2:-}"; }
fail() { printf 'FAIL %-24s %s\n' "$1" "$2" >&2; FAILURES=$((FAILURES + 1)); }

if [ ! -x "${PYTHON}" ]; then
  echo "FAIL python                   missing gateway venv: ${PYTHON}" >&2
  exit 1
fi

if PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.validate_config --config "${CONFIG_PATH}"; then
  pass config "${CONFIG_PATH}"
else
  fail config "configuration validation failed"
fi

cfg() {
  PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" -m hpr_gateway.config_value \
    --config "${CONFIG_PATH}" "$@"
}

check_unit() {
  local unit="$1"
  # Avoid grep -q under pipefail: systemctl can receive SIGPIPE after an early
  # match, intermittently reporting an installed unit as missing.
  if [ "$(systemctl show "${unit}" -p LoadState --value 2>/dev/null)" != loaded ]; then
    fail "unit:${unit}" "not installed"
    return
  fi
  if ! systemctl is-enabled "${unit}" >/dev/null 2>&1; then
    fail "unit:${unit}" "not enabled"
    return
  fi
  if systemctl is-active "${unit}" >/dev/null 2>&1; then
    pass "unit:${unit}" "active"
  elif [ "${REQUIRE_ACTIVE}" = "true" ]; then
    fail "unit:${unit}" "enabled but not active"
  else
    warn "unit:${unit}" "enabled but not active"
  fi
}

check_video_boot_policy() {
  local unit="$1"
  local required_after="$2"
  local unit_text start_limit restart_policy
  unit_text="$(systemctl cat "${unit}" 2>/dev/null || true)"
  start_limit="$(systemctl show "${unit}" -p StartLimitIntervalUSec --value 2>/dev/null || true)"
  restart_policy="$(systemctl show "${unit}" -p Restart --value 2>/dev/null || true)"

  if grep -Eq '^(After|Wants|Requires)=.*(network-online\.target|tailscaled\.service)' <<<"${unit_text}"; then
    fail "boot:${unit}" "must not depend on network-online or Tailscale"
  elif ! grep -Eq "^After=.*${required_after}" <<<"${unit_text}"; then
    fail "boot:${unit}" "missing startup dependency ${required_after}"
  elif [ "${start_limit}" != "0" ]; then
    fail "boot:${unit}" "restart attempts are rate-limited (${start_limit:-unknown})"
  elif [ "${restart_policy}" != "always" ]; then
    fail "boot:${unit}" "Restart must be always (${restart_policy:-unknown})"
  else
    pass "boot:${unit}" "local boot, unlimited retries"
  fi
}

if [ "$(cfg services.gps.enabled --default false)" = "true" ] && \
   [ "$(cfg gps.gpsd_enabled --default true)" = "true" ]; then
  gps_device="$(cfg gps.device --default auto)"
  expected_device="${gps_device}"
  [ "${expected_device}" = "auto" ] && expected_device=""

  grep -q '^USBAUTO="true"$' /etc/default/gpsd 2>/dev/null \
    && pass gpsd-hotplug "USBAUTO=true" \
    || fail gpsd-hotplug "invalid /etc/default/gpsd"
  grep -Fqx "DEVICES=\"${expected_device}\"" /etc/default/gpsd 2>/dev/null \
    && pass gpsd-device-config "${expected_device:-auto}" \
    || fail gpsd-device-config "does not match gps.device"
  if [ -n "${expected_device}" ]; then
    [ -e "${expected_device}" ] \
      && pass gps-receiver "${expected_device}" \
      || fail gps-receiver "not enumerated at ${expected_device}"
  fi
  systemctl is-enabled gpsd.socket >/dev/null 2>&1 \
    && pass gpsd.socket "enabled" \
    || fail gpsd.socket "not enabled"
  if [ "${REQUIRE_ACTIVE}" = "true" ]; then
    systemctl is-active gpsd.service >/dev/null 2>&1 \
      && systemctl is-active gpsd.socket >/dev/null 2>&1 \
      && pass gpsd "service and socket active" \
      || fail gpsd "service or socket inactive"
  fi

  unit_text="$(systemctl cat hpr-gps.service 2>/dev/null || true)"
  grep -Eq '^After=.*gpsd\.service' <<<"${unit_text}" \
    && grep -Eq '^After=.*gpsd\.socket' <<<"${unit_text}" \
    && grep -Eq '^Wants=.*gpsd\.service' <<<"${unit_text}" \
    && grep -Eq '^Wants=.*gpsd\.socket' <<<"${unit_text}" \
    && pass gps-dependencies "gpsd service/socket" \
    || fail gps-dependencies "hpr-gps.service dependencies missing"

  if [ "$(cfg gps.hotplug_recovery_enabled --default true)" = "true" ]; then
    vendor_id="$(cfg gps.usb_vendor_id --default 1546)"; vendor_id="${vendor_id#0x}"
    product_id="$(cfg gps.usb_product_id --default 01a9)"; product_id="${product_id#0x}"
    hotplug_unit=/etc/systemd/system/hpr-gps-hotplug.service
    hotplug_rule=/etc/udev/rules.d/99-hpr-gps-hotplug.rules
    if [ -f "${hotplug_unit}" ] && [ -f "${hotplug_rule}" ] \
      && grep -Fq 'Environment=GPS_DEVICE=' "${hotplug_unit}" \
      && grep -Fq 'systemctl restart gpsd.service; systemctl restart hpr-gps.service' "${hotplug_unit}" \
      && grep -Fq "ATTRS{idVendor}==\"${vendor_id}\"" "${hotplug_rule}" \
      && grep -Fq "ATTRS{idProduct}==\"${product_id}\"" "${hotplug_rule}" \
      && grep -Fq 'ENV{SYSTEMD_WANTS}="hpr-gps-hotplug.service"' "${hotplug_rule}"; then
      pass gps-hotplug-recovery "${vendor_id}:${product_id}"
    else
      fail gps-hotplug-recovery "unit or udev rule invalid"
    fi
  fi
  if [ "$(cfg gps.recovery_enabled --default true)" = "true" ]; then
    systemctl is-enabled hpr-gps-recovery.service >/dev/null 2>&1 \
      && pass gps-fix-recovery-boot "enabled" \
      || fail gps-fix-recovery-boot "not enabled"
    if [ "${REQUIRE_ACTIVE}" = "true" ]; then
      systemctl is-active --quiet hpr-gps-recovery.service \
        && pass gps-fix-recovery "10s loss / 20s reacquisition / 60s cooldown" \
        || fail gps-fix-recovery "watchdog inactive"
    fi
  fi
fi

role_output="$(PYTHONPATH="${INSTALL_ROOT}" "${PYTHON}" - "${CONFIG_PATH}" <<'PY'
import sys
from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.config import load_config

config = load_config(sys.argv[1])
for role in ("telemetry", "heart_rate"):
    print(f"{role}={resolve_bluetooth_role(config, role)}")
PY
)" || role_status=$?
role_status="${role_status:-0}"
if [ "${role_status}" -eq 0 ]; then
  telemetry_adapter="$(sed -n 's/^telemetry=//p' <<<"${role_output}")"
  heart_rate_adapter="$(sed -n 's/^heart_rate=//p' <<<"${role_output}")"
  pass bluetooth-telemetry "${telemetry_adapter}"
  pass bluetooth-heart-rate "${heart_rate_adapter}"
  if [ -n "${telemetry_adapter}" ] && [ "${telemetry_adapter}" != "${heart_rate_adapter}" ]; then
    pass bluetooth-separation "dedicated controllers"
  else
    fail bluetooth-separation "roles resolved to the same controller"
  fi
  heart_rate_controller="$(hciconfig "${heart_rate_adapter}" 2>/dev/null || true)"
  if [[ "${heart_rate_controller}" == *'UP RUNNING'* ]]; then
    pass bluetooth-scan-powered "${heart_rate_adapter} UP"
  else
    fail bluetooth-scan-powered "${heart_rate_adapter} is not UP"
  fi
else
  fail bluetooth-roles "stable controller resolution failed"
fi

if [ "$(cfg services.video.enabled --default false)" = "true" ]; then
  mediamtx_binary="${INSTALL_ROOT}/runtime/video/mediamtx"
  expected_sha="$(cfg video.mediamtx_sha256 --default '')"
  if [ -x "${mediamtx_binary}" ]; then
    actual_sha="$(sha256sum "${mediamtx_binary}" | awk '{print toupper($1)}')"
    if [ -z "${expected_sha}" ] || [ "${actual_sha}" = "$(printf '%s' "${expected_sha}" | tr '[:lower:]' '[:upper:]')" ]; then
      pass mediamtx-binary "checksum verified"
    else
      fail mediamtx-binary "checksum mismatch"
    fi
  else
    fail mediamtx-binary "missing ${mediamtx_binary}"
  fi
  check_video_boot_policy hpr-video-mediamtx.service local-fs.target
  native_video=false
  if [ -f "${INSTALL_ROOT}/runtime/video/hpr-video-native.py" ]; then
    native_video=true
    check_unit hpr-video-compositor.service
    /usr/bin/python3 - <<'PY' && pass video-progress "all available workers advancing" || fail video-progress "worker frame progress missing or stale"
import json, time
from pathlib import Path
s = json.loads(Path('/run/hpr-video/video-status.json').read_text())
assert time.monotonic() - s['at'] < 8, 'supervisor stale'
assert s['children'], 'no cameras available'
for role in ('front', 'rear'):
    if role + '-capture' in s['children']:
        assert role + '-publish' in s['children'], role + ' publisher missing'
for name, child in s['children'].items():
    assert time.monotonic() - child.get('at', 0) < 8, name
PY
  fi
  for camera in front rear; do
    if [ "$(cfg "video.cameras.${camera}.enabled" --default false)" = "true" ]; then
      device="$(cfg "video.cameras.${camera}.device" --default '')"
      [ -e "${device}" ] \
        && pass "camera:${camera}" "${device}" \
        || fail "camera:${camera}" "not enumerated at ${device}"
      if [ "${native_video}" != "true" ]; then
        check_unit "hpr-video-${camera}.service"
        check_video_boot_policy "hpr-video-${camera}.service" hpr-video-mediamtx.service
      fi
    fi
  done

fi

declare -a service_pairs=(
  'heartbeat:hpr-heartbeat.service'
  'pi_power_health:hpr-pi-power-health.service'
  'services_health:hpr-services-health.service'
  'tpms:hpr-tpms.service'
  'gps:hpr-gps.service'
  'heart_rate:hpr-heart-rate.service'
  'heart_rate:hpr-bluetooth-recovery.service'
  'power_cadence:hpr-power-cadence.service'
  'gpio_control:hpr-gpio-control.service'
  'gpio_config_sync:hpr-gpio-config-sync.service'
  'derailleur:hpr-derailleur.service'
  'power_watch:hpr-power-watch.service'
  'video:hpr-video-mediamtx.service'
)
for pair in "${service_pairs[@]}"; do
  key="${pair%%:*}"
  unit="${pair#*:}"
  if [ "${key}" = "gpio_config_sync" ] && [ -f "$(dirname "${CONFIG_PATH}")/.config-single-writer-v2" ]; then
    check_unit hpr-trike-config-sync.service
    continue
  fi
  if [ "$(cfg "services.${key}.enabled" --default false)" = "true" ]; then
    check_unit "${unit}"
  fi
done

for legacy_unit in \
  hpr-tpms-all.service hpr-hrmpro.service hpr-pedals.service \
  hpr-trike1-services-health.service mediamtx.service; do
  if systemctl is-active "${legacy_unit}" >/dev/null 2>&1; then
    fail legacy-services "${legacy_unit} is still active"
  fi
done

if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    pass tailscale "$(tailscale ip -4 2>/dev/null | head -n1)"
  else
    fail tailscale "installed but not registered"
  fi
elif [ "$(cfg tailscale.enabled --default false)" = "true" ]; then
  fail tailscale "not installed"
else
  warn tailscale "disabled"
fi

if [ "${FAILURES}" -gt 0 ]; then
  echo "Pi gateway validation failed: ${FAILURES} issue(s)." >&2
  exit 1
fi
echo "Pi gateway validation passed."

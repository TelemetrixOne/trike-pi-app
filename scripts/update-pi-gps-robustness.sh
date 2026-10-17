#!/usr/bin/env bash
# Targeted GPS/health upgrade. No central config, receiver settings or video writes.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
PYTHON="${INSTALL_ROOT}/venv/bin/python"
RULE=/etc/udev/rules.d/99-hpr-gps-hotplug.rules
BACKUP="/opt/hpr/backups/gps-robustness-$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$INSTALL_ROOT"
"$PYTHON" - "$INSTALL_ROOT" "$RULE" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
expected = {"gps.py": "720bf5c3d1a974f99c30de0f4d1db63552f9eb1c6870ed5e3afacd0fec2733a1",
            "services_health.py": "a6b970988a68bb8dfd3b51705179f6bfe1ab9c124dffe6d8b15d9472eedba163"}
for name, digest in expected.items():
    actual = hashlib.sha256((root / 'hpr_gateway/services' / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if actual != digest:
        raise SystemExit(f'Unidentified live enhancement in {name}: {actual}; refusing overwrite')
rule = pathlib.Path(sys.argv[2]).read_text()
if rule.count('ENV{SYSTEMD_WANTS}+="hpr-gps-hotplug.service"') != 1:
    raise SystemExit('Unexpected hotplug rule; refusing replacement')
PY
"$PYTHON" -m py_compile "$REPO_ROOT/hpr_gateway/services/gps.py" "$REPO_ROOT/hpr_gateway/services/services_health.py"
"$PYTHON" -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
install -d -m 0700 "$BACKUP"
cp -a "$INSTALL_ROOT/hpr_gateway/services/gps.py" "$INSTALL_ROOT/hpr_gateway/services/services_health.py" "$RULE" "$BACKUP/"
cp -a /etc/hpr/hpr.yaml /etc/default/gpsd /etc/systemd/system/hpr-gps.service /etc/systemd/system/hpr-gps-hotplug.service "$BACKUP/"
restore_on_failure() {
  trap - ERR
  cp -a "$BACKUP/gps.py" "$BACKUP/services_health.py" "$INSTALL_ROOT/hpr_gateway/services/"
  cp -a "$BACKUP/99-hpr-gps-hotplug.rules" "$RULE"
  udevadm control --reload-rules
  systemctl restart gpsd.service hpr-gps.service hpr-services-health.service
  echo "Deployment failed; prior files restored from $BACKUP" >&2
}
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/services/gps.py" "$INSTALL_ROOT/hpr_gateway/services/gps.py"
install -m 0644 "$REPO_ROOT/hpr_gateway/services/services_health.py" "$INSTALL_ROOT/hpr_gateway/services/services_health.py"
"$PYTHON" - "$RULE" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
path.write_text(path.read_text().replace('ENV{SYSTEMD_WANTS}+="hpr-gps-hotplug.service"',
                                       'ENV{SYSTEMD_WANTS}="hpr-gps-hotplug.service"'))
PY
# Reload only: never retrigger all USB devices or interrupt cameras/BLE.
udevadm control --reload-rules
systemctl restart gpsd.service hpr-gps.service hpr-services-health.service
for unit in gpsd.service hpr-gps.service hpr-services-health.service; do
  systemctl is-active --quiet "$unit"
done
"$PYTHON" "$REPO_ROOT/scripts/validate-pi-gps-robustness.py" "$@"
trap - ERR
echo "GPS robustness deployed; backup: $BACKUP"

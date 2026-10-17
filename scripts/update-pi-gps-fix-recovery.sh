#!/usr/bin/env bash
# Install the GPS-only watchdog without restarting a healthy GPS receiver.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
UNIT=/etc/systemd/system/hpr-gps-recovery.service
MODULE="$INSTALL_ROOT/hpr_gateway/services/gps_recovery.py"
HEALTH="$INSTALL_ROOT/hpr_gateway/services/services_health.py"
PYTHON="$INSTALL_ROOT/venv/bin/python"
BACKUP="/opt/hpr/backups/gps-fix-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$INSTALL_ROOT"
[[ ! -e "$UNIT" && ! -e "$MODULE" ]] || { echo 'Unexpected existing watchdog; refusing overwrite' >&2; exit 1; }
"$PYTHON" - "$HEALTH" "$INSTALL_ROOT/hpr_gateway/services/gps.py" <<'PY'
import hashlib, pathlib, sys
expected = ['c283d331fa77883e93e63aab14959761c4d98e0528d7a7a335151b2ca3fd4e2d',
            'e2f1b223b847c5ad83f8a1284ad28f4c45d654bd29fa95a99637f3357cc756d0']
for name, digest in zip(sys.argv[1:], expected):
    if hashlib.sha256(pathlib.Path(name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != digest:
        raise SystemExit(f'Unexpected live source: {name}; refusing overwrite')
PY
"$PYTHON" -m py_compile "$REPO_ROOT/hpr_gateway/services/gps_recovery.py" "$REPO_ROOT/hpr_gateway/services/services_health.py"
"$PYTHON" -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
install -d -m 0700 "$BACKUP"
cp -a "$HEALTH" /etc/hpr/hpr.yaml /etc/default/gpsd /etc/systemd/system/hpr-gps.service "$BACKUP/"
restore_on_failure() {
  trap - ERR
  systemctl disable --now hpr-gps-recovery.service || true
  cp -a "$BACKUP/services_health.py" "$HEALTH"
  rm -f "$UNIT" "$MODULE"
  systemctl daemon-reload
  systemctl restart hpr-services-health.service
  echo "Watchdog install failed; restored prior state from $BACKUP" >&2
}
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/services/gps_recovery.py" "$MODULE"
install -m 0644 "$REPO_ROOT/hpr_gateway/services/services_health.py" "$HEALTH"
sed "s|/opt/hpr/gateway|${INSTALL_ROOT}|g" "$REPO_ROOT/systemd/hpr-gps-recovery.service" > "$UNIT"
systemd-analyze verify "$UNIT"
systemctl daemon-reload
systemctl enable --now hpr-gps-recovery.service
systemctl restart hpr-services-health.service
systemctl is-active --quiet hpr-gps-recovery.service
"$PYTHON" "$REPO_ROOT/scripts/validate-pi-gps-robustness.py" --require-fix --require-recovery
trap - ERR
echo "GPS fix recovery armed; backup: $BACKUP"

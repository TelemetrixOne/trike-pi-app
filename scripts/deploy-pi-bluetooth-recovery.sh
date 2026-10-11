#!/usr/bin/env bash
# Install only isolated HRM scanner recovery, leaving other BLE users untouched.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
MODULE="${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
VALIDATOR="${INSTALL_ROOT}/scripts/validate-pi-gateway.sh"
UNIT="/etc/systemd/system/hpr-bluetooth-recovery.service"
BACKUP="/opt/hpr/backups/bluetooth-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
EXPECTED_VALIDATOR_SHA256="1f282a986741098f438fa9077f0b82b41b74451ed0da96745e24795fc895de19"

[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ ! -e "$MODULE" && ! -e "$UNIT" ]] || { echo 'Unexpected live recovery module or unit; refusing to overwrite' >&2; exit 1; }
python3 - "$VALIDATOR" "$EXPECTED_VALIDATOR_SHA256" <<'PY'
import hashlib
import pathlib
import sys

actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f"Live validator differs from reviewed GitHub main ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
PYTHONPATH="$INSTALL_ROOT" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
install -d -m 0700 "$BACKUP"
cp -a "$VALIDATOR" "$BACKUP/validate-pi-gateway.sh"
restore_on_failure() {
  systemctl disable --now hpr-bluetooth-recovery.service 2>/dev/null || true
  rm -f -- "$MODULE" "$UNIT"
  cp -a "$BACKUP/validate-pi-gateway.sh" "$VALIDATOR"
  systemctl daemon-reload
}
trap restore_on_failure ERR
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "$MODULE"
install -m 0755 "${REPO_ROOT}/scripts/validate-pi-gateway.sh" "$VALIDATOR"
install -m 0644 "${REPO_ROOT}/systemd/hpr-bluetooth-recovery.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now hpr-bluetooth-recovery.service
systemctl is-active --quiet hpr-bluetooth-recovery.service
trap - ERR
echo "HRM scanner recovery installed; no existing services restarted. Backup: $BACKUP"

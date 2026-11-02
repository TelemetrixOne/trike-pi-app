#!/usr/bin/env bash
# Targeted HRM handover deploy; never replaces unrelated /opt/hpr files.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
HRM="${INSTALL_ROOT}/hpr_gateway/services/heart_rate.py"
RECOVERY="${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
BACKUP="/opt/hpr/backups/hrm-handover-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$HRM" "$RECOVERY" <<'PY'
import hashlib
import pathlib
import sys

expected = (
    "457eeb0a2d05aeeb7ce4d6cbf4def1de6faa38eefe11052bfd8b00e81be81d8d",
    "d859c4d0a1e31b0e40d27539fe13a1e805b2a26f82b97a20f1aa40e42ba99aa4",
)
for name, digest in zip(sys.argv[1:], expected):
    actual = hashlib.sha256(pathlib.Path(name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != digest:
        raise SystemExit(f"Unexpected live file {name} ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile \
  "${REPO_ROOT}/hpr_gateway/services/heart_rate.py" \
  "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
PYTHONPATH="$INSTALL_ROOT" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
install -d -m 0700 "$BACKUP"
cp -a "$HRM" "$RECOVERY" "$BACKUP/"
restore_on_failure() {
  cp -a "$BACKUP/heart_rate.py" "$HRM"
  cp -a "$BACKUP/bluetooth_recovery.py" "$RECOVERY"
}
trap restore_on_failure ERR
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/heart_rate.py" "$HRM"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "$RECOVERY"
trap - ERR
systemctl restart hpr-bluetooth-recovery.service
if ! systemctl restart hpr-heart-rate.service; then
  echo 'HRM service could not start; check the dedicated USB scanner hardware' >&2
fi
echo "Continuous HRM handover deployed. Backup: $BACKUP"

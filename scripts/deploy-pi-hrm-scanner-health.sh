#!/usr/bin/env bash
# Targeted upgrade from continuous HRM handover to scan fault recovery.
set -euo pipefail
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
    "f1a816ce0dc3b987441dfca00dd08838c5668803cd5701918b7e7046663021e1",
    "d28f8878f37749514ff6a89bde0dc1dacc077e8e45d9432d31509500d9e43414",
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
  systemctl restart hpr-heart-rate.service
  systemctl restart hpr-bluetooth-recovery.service
}
trap restore_on_failure ERR
systemctl stop hpr-bluetooth-recovery.service
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/heart_rate.py" "$HRM"
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "$RECOVERY"
systemctl restart hpr-heart-rate.service
sleep 2
systemctl start hpr-bluetooth-recovery.service
trap - ERR
echo "HRM scanner health deployed. Backup: $BACKUP"

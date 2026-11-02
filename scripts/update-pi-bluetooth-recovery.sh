#!/usr/bin/env bash
# Advance the targeted recovery from 18bc99c, preserving exact live originals.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
RECOVERY="${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
BLUETOOTH="${INSTALL_ROOT}/hpr_gateway/bluetooth.py"
BACKUP="/opt/hpr/backups/bluetooth-recovery-update-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$RECOVERY" "$BLUETOOTH" <<'PY'
import hashlib
import pathlib
import sys

expected = (
    "a0d5d09d5d71b97d1759546c8d197645eaf6671398fefb6e4f36b2c3e2452eb9",
    "555a39de214caded8f1228b6ab1da360702f05772b2d11f52e64ac2d217bab63",
)
for name, digest in zip(sys.argv[1:], expected):
    actual = hashlib.sha256(pathlib.Path(name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != digest:
        raise SystemExit(f"Unexpected live file {name} ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "${REPO_ROOT}/hpr_gateway/bluetooth.py"
install -d -m 0700 "$BACKUP"
cp -a "$RECOVERY" "$BLUETOOTH" "$BACKUP/"
restore_on_failure() {
  cp -a "$BACKUP/bluetooth_recovery.py" "$RECOVERY"
  cp -a "$BACKUP/bluetooth.py" "$BLUETOOTH"
  systemctl restart hpr-bluetooth-recovery.service
}
trap restore_on_failure ERR
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "$RECOVERY"
install -m 0644 "${REPO_ROOT}/hpr_gateway/bluetooth.py" "$BLUETOOTH"
systemctl restart hpr-bluetooth-recovery.service
systemctl is-active --quiet hpr-bluetooth-recovery.service
trap - ERR
echo "Scanner watchdog and controller resolver updated; only watchdog restarted. Backup: $BACKUP"

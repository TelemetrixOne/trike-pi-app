#!/usr/bin/env bash
# Advance the deployed scan-health recovery without touching other services.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
RECOVERY="${INSTALL_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
BACKUP="/opt/hpr/backups/bluetooth-scan-health-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$RECOVERY" <<'PY'
import hashlib
import pathlib
import sys

actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
expected = "15c4f16b4b35833420cf7ebc47a0085d8a91bc9ae936f9404882c2053f05fe11"
if actual != expected:
    raise SystemExit(f"Unexpected live recovery module ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py"
install -d -m 0700 "$BACKUP"
cp -a "$RECOVERY" "$BACKUP/bluetooth_recovery.py"
restore_on_failure() {
  cp -a "$BACKUP/bluetooth_recovery.py" "$RECOVERY"
  systemctl restart hpr-bluetooth-recovery.service
}
trap restore_on_failure ERR
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/bluetooth_recovery.py" "$RECOVERY"
systemctl restart hpr-bluetooth-recovery.service
systemctl is-active --quiet hpr-bluetooth-recovery.service
trap - ERR
echo "Live scan-health recovery installed; only watchdog restarted. Backup: $BACKUP"

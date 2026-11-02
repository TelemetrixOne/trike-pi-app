#!/usr/bin/env bash
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT=/opt/hpr/gateway
BACKUP="/opt/hpr/backups/system-metrics-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$INSTALL_ROOT" <<'PY'
from pathlib import Path
import hashlib, sys
root = Path(sys.argv[1])
target = root / 'hpr_gateway/services/services_health.py'
actual = hashlib.sha256(target.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
initial = 'b9f7b0b11020cb659d946da4b44ca65f23a297e2f3f45f45b1461ed5210588a3'
previous = 'a6b970988a68bb8dfd3b51705179f6bfe1ab9c124dffe6d8b15d9472eedba163'
if actual not in (initial, previous):
    raise SystemExit(f'Unexpected live service-health code: {actual}; refusing overwrite')
runtime = root / 'hpr_gateway/runtime_health.py'
if actual == initial and runtime.exists():
    raise SystemExit('Unidentified live runtime_health.py; refusing overwrite')
if actual == previous:
    digest = hashlib.sha256(runtime.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if digest != 'c4ed4ac661b01f2d917dc7a66b42345a09351c968d58ceb357148c9cd2ea22d9':
        raise SystemExit('Unexpected live runtime collector; refusing overwrite')
PY
"$INSTALL_ROOT/venv/bin/python" -m py_compile \
  "$REPO_ROOT/hpr_gateway/runtime_health.py" "$REPO_ROOT/hpr_gateway/services/services_health.py"
install -d -m 0700 "$BACKUP"
cp -a "$INSTALL_ROOT/hpr_gateway/services/services_health.py" "$BACKUP/services_health.py"
if [ -e "$INSTALL_ROOT/hpr_gateway/runtime_health.py" ]; then
  cp -a "$INSTALL_ROOT/hpr_gateway/runtime_health.py" "$BACKUP/runtime_health.py"
fi
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat hpr-services-health.service > "$BACKUP/unit.txt"
cp -a "$REPO_ROOT/scripts/rollback-pi-system-metrics.sh" "$BACKUP/rollback.sh"
restore_on_failure() { bash "$BACKUP/rollback.sh" "$BACKUP"; }
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/runtime_health.py" "$INSTALL_ROOT/hpr_gateway/runtime_health.py"
install -m 0644 "$REPO_ROOT/hpr_gateway/services/services_health.py" "$INSTALL_ROOT/hpr_gateway/services/services_health.py"
systemctl restart hpr-services-health.service
sleep 2
systemctl is-active --quiet hpr-services-health.service
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
trap - ERR
echo "System metrics deployed; only service-health publisher restarted. Backup: $BACKUP"

#!/usr/bin/env bash
# Correct health evidence only: keep the established pedal GATT link intact.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
HEALTH="$INSTALL_ROOT/hpr_gateway/services/services_health.py"
PYTHON="$INSTALL_ROOT/venv/bin/python"
BACKUP="/opt/hpr/backups/pedal-health-$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$INSTALL_ROOT"
"$PYTHON" - "$HEALTH" <<'PY'
import hashlib, pathlib, sys
digest = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
if digest != 'd3436e03665b3f74b3b7f198fa36528eb323d6c1e8516bec139e4463a2cdd86f':
    raise SystemExit(f'Unidentified live health enhancement ({digest}); refusing overwrite')
PY
"$PYTHON" -m py_compile "$REPO_ROOT/hpr_gateway/services/services_health.py"
"$PYTHON" -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
install -d -m 0700 "$BACKUP"
cp -a "$HEALTH" /etc/hpr/hpr.yaml "$BACKUP/"
restore_on_failure() {
  trap - ERR
  cp -a "$BACKUP/services_health.py" "$HEALTH"
  systemctl restart hpr-services-health.service
  echo "Pedal health update failed; prior health source restored from $BACKUP" >&2
}
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/services/services_health.py" "$HEALTH"
systemctl restart hpr-services-health.service
systemctl is-active --quiet hpr-services-health.service
"$PYTHON" "$REPO_ROOT/scripts/validate-pi-pedal-health.py"
trap - ERR
echo "Pedal health updated; only health publisher restarted. Backup: $BACKUP"

#!/usr/bin/env bash
# DRS-only source update; the shared owner restart needs stationary approval.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
[[ ! -e "$ROOT/hpr_gateway/drs_protocol.py" ]] || { echo 'Unidentified existing DRS protocol; stop' >&2; exit 1; }
python3 - "$ROOT/hpr_gateway/services/gpio_control.py" <<'PY'
import hashlib, pathlib, sys
raw=pathlib.Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(b'\n'.join(raw.splitlines())+b'\n').hexdigest() == 'f36649e6b21fc64894d2cc99ef118e0c51c94f43e7ff9bbaab694d57ce02dfc1', 'Unexpected live GPIO source; preserve and investigate'
PY
"$ROOT/venv/bin/python" -m py_compile "$REPO_ROOT/hpr_gateway/drs_protocol.py" "$REPO_ROOT/hpr_gateway/services/gpio_control.py"
systemctl is-active --quiet hpr-gpio-control.service
BACKUP="/opt/hpr/backups/drs-reliability-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/hpr_gateway/services/gpio_control.py" "$BACKUP/gpio_control.py"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat hpr-gpio-control.service > "$BACKUP/unit.txt"
restore() {
  trap - ERR
  cp -a "$ROOT/hpr_gateway/services/gpio_control.py" "$BACKUP/failed-gpio_control.py"
  cp -a "$BACKUP/gpio_control.py" "$ROOT/hpr_gateway/services/gpio_control.py"
  # Keep the unused additive helper for diagnosis rather than deleting it.
  systemctl restart hpr-gpio-control.service
  echo "DRS deployment failed; previous source restored from $BACKUP" >&2
}
trap restore ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/drs_protocol.py" "$ROOT/hpr_gateway/drs_protocol.py"
install -m 0644 "$REPO_ROOT/hpr_gateway/services/gpio_control.py" "$ROOT/hpr_gateway/services/gpio_control.py"
systemctl restart hpr-gpio-control.service
PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-drs-reliability.py"
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
trap - ERR
echo "DRS reliability deployed. Only shared GPIO owner restarted; no configuration changes. Backup: $BACKUP"

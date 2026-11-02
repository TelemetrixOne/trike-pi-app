#!/usr/bin/env bash
# Scoped source replacement. Shared GPIO owner restart can reinitialise outputs.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ "${1:-}" == --stationary-approved ]] || {
  echo 'Stop: confirm stationary/shared-GPIO-restart approval before using --stationary-approved' >&2
  exit 1
}
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
python3 - "$ROOT" <<'PY'
import hashlib, pathlib, sys
root=pathlib.Path(sys.argv[1])
expected={
    'hpr_gateway/config.py': '7844219410063916c4613be8285b74ca5a5e7307ba95ae5689d59391b0a8b0ac',
    'hpr_gateway/services/gpio_control.py': 'ffe4a40e52667deeb33c2808d54340197cd51648767c27c816e5f3fd9aa7d6dd',
}
for name, digest in expected.items():
    raw=(root/name).read_bytes()
    assert hashlib.sha256(b'\n'.join(raw.splitlines())+b'\n').hexdigest()==digest, 'Unidentified live enhancement: '+name
PY
"$ROOT/venv/bin/python" -m py_compile "$REPO_ROOT/hpr_gateway/config.py" "$REPO_ROOT/hpr_gateway/services/gpio_control.py"
systemctl is-active --quiet hpr-gpio-control.service
BACKUP="/opt/hpr/backups/native-mqtt-only-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/hpr_gateway/config.py" "$BACKUP/config.py"
cp -a "$ROOT/hpr_gateway/services/gpio_control.py" "$BACKUP/gpio_control.py"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat hpr-gpio-control.service > "$BACKUP/unit.txt"
restore() {
  trap - ERR
  cp -a "$BACKUP/config.py" "$ROOT/hpr_gateway/config.py"
  cp -a "$BACKUP/gpio_control.py" "$ROOT/hpr_gateway/services/gpio_control.py"
  systemctl restart hpr-gpio-control.service
  echo "Source restored after failed validation. Current config preserved. Backup: $BACKUP" >&2
}
trap restore ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/config.py" "$ROOT/hpr_gateway/config.py"
install -m 0644 "$REPO_ROOT/hpr_gateway/services/gpio_control.py" "$ROOT/hpr_gateway/services/gpio_control.py"
systemctl restart hpr-gpio-control.service
PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$REPO_ROOT/scripts/validate-pi-drs-reliability.py"
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
trap - ERR
echo "Native MQTT-only source deployed; only shared GPIO owner restarted. Backup: $BACKUP"

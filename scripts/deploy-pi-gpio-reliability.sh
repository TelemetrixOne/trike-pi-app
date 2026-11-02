#!/usr/bin/env bash
# Replace only the reviewed GPIO controller and profile validator.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
BACKUP="/opt/hpr/backups/gpio-reliability-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$INSTALL_ROOT" <<'PY'
import hashlib
from pathlib import Path
import sys
expected = {
    "hpr_gateway/services/gpio_control.py": "5b606db2d4a7ebf714c19a6948bd50c7a5989876cb64360dce2c2dcdaf27e3f6",
    "hpr_gateway/gpio_profile.py": "13213687d235f9c3ff85568659522cf488ee5656c6685ef76838b23a16f6f677",
}
for relative, digest in expected.items():
    target = Path(sys.argv[1]) / relative
    actual = hashlib.sha256(target.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != digest:
        raise SystemExit(f"Unexpected live file {target} ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile \
    "${REPO_ROOT}/hpr_gateway/services/gpio_control.py" \
    "${REPO_ROOT}/hpr_gateway/gpio_profile.py"
PYTHONPATH="$INSTALL_ROOT" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config "$CONFIG_PATH"
install -d -m 0700 "$BACKUP"
cp -a "${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py" "$BACKUP/gpio_control.py"
cp -a "${INSTALL_ROOT}/hpr_gateway/gpio_profile.py" "$BACKUP/gpio_profile.py"
cp -a "$CONFIG_PATH" "$BACKUP/hpr.yaml"
systemctl cat hpr-gpio-control.service hpr-gpio-config-sync.service hpr-trike-config-sync.service > "$BACKUP/units.txt"
restore_on_failure() {
    cp -a "$BACKUP/gpio_control.py" "${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py"
    cp -a "$BACKUP/gpio_profile.py" "${INSTALL_ROOT}/hpr_gateway/gpio_profile.py"
    systemctl restart hpr-gpio-control.service
    systemctl try-restart hpr-gpio-config-sync.service hpr-trike-config-sync.service
}
trap restore_on_failure ERR
install -m 0644 "${REPO_ROOT}/hpr_gateway/services/gpio_control.py" "${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py"
install -m 0644 "${REPO_ROOT}/hpr_gateway/gpio_profile.py" "${INSTALL_ROOT}/hpr_gateway/gpio_profile.py"
PYTHONPATH="$INSTALL_ROOT" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config "$CONFIG_PATH"
systemctl restart hpr-gpio-control.service
systemctl is-active --quiet hpr-gpio-control.service
# Both consumers import the profile validator; only restart already-running units.
systemctl try-restart hpr-gpio-config-sync.service hpr-trike-config-sync.service
trap - ERR
echo "GPIO reliability deployed. Config preserved. Backup: $BACKUP"

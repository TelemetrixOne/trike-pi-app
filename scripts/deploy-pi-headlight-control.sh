#!/usr/bin/env bash
# Deploy only the reviewed GPIO controller change; preserve the previous file.
set -euo pipefail

# Refuse operator maintenance while centrally session-protected.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
SOURCE="${REPO_ROOT}/hpr_gateway/services/gpio_control.py"
TARGET="${INSTALL_ROOT}/hpr_gateway/services/gpio_control.py"
BACKUP="/opt/hpr/backups/headlight-control-$(date -u +%Y%m%dT%H%M%SZ)"
EXPECTED_OLD_SHA256="43162912fca5e255a49618ff1feeb152010224e6aaca3ddf4fd5a1d58642514a"

[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -f "$SOURCE" && -f "$TARGET" ]] || { echo 'Source or live GPIO controller missing' >&2; exit 1; }
python3 - "$TARGET" "$EXPECTED_OLD_SHA256" <<'PY'
import hashlib
import pathlib
import sys

actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f"Live GPIO controller differs from reviewed GitHub main ({actual}); refusing to overwrite")
PY
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "$SOURCE"
PYTHONPATH="$INSTALL_ROOT" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config "$CONFIG_PATH"
install -d -m 0700 "$BACKUP"
cp -a "$TARGET" "$BACKUP/gpio_control.py"
restore_on_failure() {
  cp -a "$BACKUP/gpio_control.py" "$TARGET"
  systemctl restart hpr-gpio-control.service
}
trap restore_on_failure ERR
install -m 0644 "$SOURCE" "$TARGET"
systemctl restart hpr-gpio-control.service
systemctl is-active --quiet hpr-gpio-control.service
trap - ERR
echo "GPIO controller deployed; only hpr-gpio-control.service restarted. Backup: $BACKUP"

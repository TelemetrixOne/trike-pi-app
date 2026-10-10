#!/usr/bin/env bash
# Install only the operator's local on-demand demo command.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${HPR_INSTALL_ROOT:-/opt/hpr/gateway}"
CONFIG_PATH="${HPR_CONFIG_PATH:-/etc/hpr/hpr.yaml}"
SOURCE="${REPO_ROOT}/bin/hpr-demo-sequence.py"
TARGET="${INSTALL_ROOT}/bin/hpr-demo-sequence.py"
BACKUP="/opt/hpr/backups/demo-trigger-$(date -u +%Y%m%dT%H%M%SZ)"

[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ -f "$SOURCE" ]] || { echo "Missing source: $SOURCE" >&2; exit 1; }
"${INSTALL_ROOT}/venv/bin/python" -m py_compile "$SOURCE"
PYTHONPATH="${INSTALL_ROOT}" "${INSTALL_ROOT}/venv/bin/python" -m hpr_gateway.validate_config --config "$CONFIG_PATH"
systemctl is-active --quiet hpr-demo-show.service || { echo 'Demo listener is not active' >&2; exit 1; }
"${INSTALL_ROOT}/venv/bin/python" "$SOURCE" --help >/dev/null

install -d -m 0700 "$BACKUP"
if [[ -e "$TARGET" ]]; then
  cp -a "$TARGET" "$BACKUP/hpr-demo-sequence.py"
fi
install -d -m 0755 "$(dirname "$TARGET")"
install -m 0755 "$SOURCE" "$TARGET"
"${INSTALL_ROOT}/venv/bin/python" "$TARGET" --help >/dev/null
echo "Demo command installed at $TARGET. No services restarted or demo triggered. Backup: $BACKUP"

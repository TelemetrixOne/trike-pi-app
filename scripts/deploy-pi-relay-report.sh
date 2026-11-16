#!/usr/bin/env bash
# Install relay reporting with one approved GPIO service restart.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"

[[ "${1:-}" == --stationary-restart-approved ]] || { echo 'Stationary GPIO restart approval required.' >&2; exit 2; }
ROOT=/opt/hpr/gateway
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELATIVE=hpr_gateway/services/gpio_control.py
TARGET="$ROOT/$RELATIVE"
CONFIG=/etc/hpr/hpr.yaml
BACKUP="/opt/hpr/backups/relay-report-$(date -u +%Y%m%dT%H%M%SZ)"
[[ -f "$TARGET" && ! -L "$TARGET" ]]
echo "85ffaa73bbc3df6a3a4552c55f56861884b756e98b4eb7665d5ee5847cb92ccb  $TARGET" | sha256sum -c -
[[ ! -e "$TARGET.relay-report-new" ]]
"$ROOT/venv/bin/python" -m py_compile "$REPO/$RELATIVE"

fingerprint() {
    systemctl show 'hpr-*.service' -p Id -p MainPID -p NRestarts -p ActiveState |
        awk -v RS='' '$0 !~ /Id=hpr-gpio-control.service\n?($|\n)/' | sort
}
before="$(fingerprint)"
config_before="$(sha256sum "$CONFIG")"
systemctl is-active --quiet hpr-gpio-control.service
install -d -m 0700 "$BACKUP"
cp -a "$TARGET" "$BACKUP/gpio_control.py"
systemctl cat hpr-gpio-control.service > "$BACKUP/unit.txt"
install -m 0644 "$REPO/$RELATIVE" "$TARGET.relay-report-new"
mv -T -- "$TARGET.relay-report-new" "$TARGET"
systemctl restart hpr-gpio-control.service
systemctl is-active --quiet hpr-gpio-control.service
[[ "$config_before" == "$(sha256sum "$CONFIG")" ]]
[[ "$before" == "$(fingerprint)" ]]
printf 'GPIO report deployed. Backup %s\n' "$BACKUP"

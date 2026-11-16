#!/usr/bin/env bash
# Reapply the 30 October GPIO runtime after the temporary diagnostic rollback.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"

[[ "${1:-}" == --stationary-restart-approved ]] || { echo 'Stationary GPIO restart approval required.' >&2; exit 2; }
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
RELATIVE=hpr_gateway/services/gpio_control.py
SOURCE="$REPO/$RELATIVE"
TARGET="$ROOT/$RELATIVE"
UNIT=hpr-gpio-control.service
UNIT_FILE="/etc/systemd/system/$UNIT"
DROPIN="${UNIT_FILE}.d/90-pre-isolation-runtime.conf"
BACKUP="/opt/hpr/backups/gpio-october-restore-$(date -u +%Y%m%dT%H%M%SZ)"
PYTHON="$ROOT/venv/bin/python"

[[ ! -L "$TARGET" && ! -L "$DROPIN" && ! -e "$TARGET.restore-new" ]]
[[ "$(systemctl show "$UNIT" -p FragmentPath --value)" == "$UNIT_FILE" ]]
[[ "$(systemctl show "$UNIT" -p DropInPaths --value)" == "$DROPIN" ]]
[[ "$(systemctl show "$UNIT" -p User --value)" == admin ]]
[[ "$(readlink "$ROOT/venv")" == /opt/hpr/candidates/runtime-2ee99a1 ]]
[[ $(sed -n '/^ExecStart=/p' "$UNIT_FILE") == "ExecStart=$PYTHON -m hpr_gateway.services.gpio_control --config /etc/hpr/hpr.yaml" ]]
sha256sum -c - <<EOF
6efc2935536497a5071895368d83e9d99ef7b424ead5dc106e11c6f9df7df429  $TARGET
85ffaa73bbc3df6a3a4552c55f56861884b756e98b4eb7665d5ee5847cb92ccb  $SOURCE
634c16afc126617ba938d859f79033762df0e942bfbfb08ca1e350803e26f998  $ROOT/hpr_gateway/credential_reload.py
6d7417689704a8c7bd11dba92cf0ff6f4127c609650208eac8f74ba79c9853d5  $DROPIN
EOF
"$PYTHON" -m py_compile "$SOURCE"

fingerprint() {
    systemctl show 'hpr-*.service' -p Id -p MainPID -p NRestarts -p ActiveState |
        awk -v RS='' '$0 !~ /Id=hpr-gpio-control.service\n?($|\n)/' | sort
}
before="$(fingerprint)"
config_before="$(sha256sum /etc/hpr/hpr.yaml)"
install -d -m 0700 "$BACKUP"
cp -a "$TARGET" /etc/hpr/hpr.yaml "$BACKUP/"
systemctl cat "$UNIT" > "$BACKUP/unit.txt"
install -m 0644 "$SOURCE" "$TARGET.restore-new"
mv -T -- "$TARGET.restore-new" "$TARGET"
mv -- "$DROPIN" "$BACKUP/"
systemd-analyze verify "$UNIT_FILE"
systemctl daemon-reload
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"
[[ "$(systemctl show "$UNIT" -p ExecStart --value)" == *"$PYTHON"* ]]
[[ "$config_before" == "$(sha256sum /etc/hpr/hpr.yaml)" ]]
[[ "$before" == "$(fingerprint)" ]]
printf 'October GPIO runtime restored. Backup %s\n' "$BACKUP"

#!/usr/bin/env bash
# Restore the native MQTT GPIO controller and only its former Python environment.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"

[[ "${1:-}" == --stationary-restart-approved ]] || { echo 'Stationary GPIO restart approval required.' >&2; exit 2; }
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
SOURCE="$REPO/hpr_gateway/services/gpio_control.py"
TARGET="$ROOT/hpr_gateway/services/gpio_control.py"
UNIT=hpr-gpio-control.service
UNIT_FILE="/etc/systemd/system/$UNIT"
DROPIN="${UNIT_FILE}.d/90-pre-isolation-runtime.conf"
OLD_VENV=/opt/hpr/backups/isolated-runtime-20261030T033539Z/venv
GPIO_VENV=/opt/hpr/candidates/gpio-pre-20261030
BACKUP="/opt/hpr/backups/gpio-october-rollback-$(date -u +%Y%m%dT%H%M%SZ)"

[[ ! -L "$TARGET" && ! -e "$TARGET.rollback-new" ]]
[[ ! -e "$GPIO_VENV" && ! -L "$GPIO_VENV" && ! -e "$DROPIN" && ! -L "$DROPIN" ]]
[[ "$(systemctl show "$UNIT" -p FragmentPath --value)" == "$UNIT_FILE" ]]
[[ -z "$(systemctl show "$UNIT" -p DropInPaths --value)" ]]
[[ "$(systemctl show "$UNIT" -p User --value)" == admin ]]
[[ "$(readlink "$ROOT/venv")" == /opt/hpr/candidates/runtime-2ee99a1 ]]
echo "593f318a6ef92c561a6bfc633c3fddd9e0766c6e26bc6af29f0b4b3abb1445d7  $TARGET" | sha256sum -c -
echo "6efc2935536497a5071895368d83e9d99ef7b424ead5dc106e11c6f9df7df429  $SOURCE" | sha256sum -c -
cmp -s "$SOURCE" "$(dirname "$OLD_VENV")/hpr_gateway/services/gpio_control.py"
grep -Fxq 'include-system-site-packages = true' "$OLD_VENV/pyvenv.cfg"

fingerprint() {
    systemctl show 'hpr-*.service' -p Id -p MainPID -p NRestarts -p ActiveState |
        awk -v RS='' '$0 !~ /Id=hpr-gpio-control.service\n?($|\n)/' | sort
}
before="$(fingerprint)"
config_before="$(sha256sum /etc/hpr/hpr.yaml)"
install -d -m 0700 "$BACKUP"
cp -a "$TARGET" "$BACKUP/gpio_control.py"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat "$UNIT" > "$BACKUP/unit.txt"

cp -a "$OLD_VENV" "$GPIO_VENV"
runuser -u admin -- env PYTHONDONTWRITEBYTECODE=1 "$GPIO_VENV/bin/python" -c '
import sys
import gpiozero, lgpio, paho.mqtt.client, adafruit_ads1x15.ads1115
from pathlib import Path
assert Path(sys.prefix) == Path(sys.argv[1]), sys.prefix
source = Path(sys.argv[2])
compile(source.read_bytes(), str(source), "exec")
' "$GPIO_VENV" "$SOURCE"

install -D -m 0644 "$REPO/systemd/$UNIT.d/90-pre-isolation-runtime.conf" "$DROPIN"
systemd-analyze verify "$UNIT_FILE"
install -m 0644 "$SOURCE" "$TARGET.rollback-new"
mv -T -- "$TARGET.rollback-new" "$TARGET"
systemctl daemon-reload
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"
[[ "$(systemctl show "$UNIT" -p ExecStart --value)" == *"$GPIO_VENV/bin/python"* ]]
[[ "$config_before" == "$(sha256sum /etc/hpr/hpr.yaml)" ]]
[[ "$before" == "$(fingerprint)" ]]
printf 'GPIO rollback deployed. Backup %s\n' "$BACKUP"

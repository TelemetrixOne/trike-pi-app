#!/usr/bin/env bash
# Install the battery estimator capability; activation stays centrally owned.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-guard.sh"

[[ "${1:-}" == --stationary-restart-approved ]] || { echo 'Stationary GPIO/config-sync restart approval required.' >&2; exit 2; }
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/hpr/gateway
CONFIG=/etc/hpr/hpr.yaml
DROPIN=/etc/systemd/system/hpr-gpio-control.service.d/50-battery-soc.conf
BACKUP="/opt/hpr/backups/battery-soc-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$DROPIN" && ! -L "$DROPIN" ]]
[[ ! -e "$ROOT/hpr_gateway/battery_soc.py" && ! -e "$ROOT/hpr_gateway/battery_protocol.py" ]]
sha256sum -c - <<EOF
85ffaa73bbc3df6a3a4552c55f56861884b756e98b4eb7665d5ee5847cb92ccb  $ROOT/hpr_gateway/services/gpio_control.py
a0f4f3cbbbdfa58fe98c82bf11ebc6eb086bdb27e513a841e6f3a4328d3ee9cc  $ROOT/hpr_gateway/gpio_profile.py
745adf6f7423cf64d8a417072eaf7c4eebcab36a00882e8251b8d7a5840c09f5  $ROOT/hpr_gateway/demo_protocol.py
EOF
"$ROOT/venv/bin/python" -m compileall -q "$REPO/hpr_gateway/battery_soc.py" "$REPO/hpr_gateway/battery_protocol.py" "$REPO/hpr_gateway/services/gpio_control.py"

fingerprint() {
    systemctl show 'hpr-*.service' -p Id -p MainPID -p NRestarts -p ActiveState |
        awk -v RS='' '$0 !~ /Id=hpr-(gpio-control|trike-config-sync).service\n?($|\n)/' | sort
}
before="$(fingerprint)"
config_before="$(sha256sum "$CONFIG")"
FILES=(hpr_gateway/battery_soc.py hpr_gateway/battery_protocol.py hpr_gateway/gpio_profile.py hpr_gateway/services/gpio_control.py)
for path in "${FILES[@]}"; do
    [[ -f "$REPO/$path" && ! -L "$ROOT/$path" && ! -e "$ROOT/$path.battery-new" ]]
done
install -d -m 0700 "$BACKUP"
cp -a "$CONFIG" "$BACKUP/hpr.yaml"
systemctl cat hpr-gpio-control.service hpr-trike-config-sync.service > "$BACKUP/units.txt"
for path in "${FILES[@]}"; do
    if [[ -e "$ROOT/$path" ]]; then
        install -D -m 0600 "$ROOT/$path" "$BACKUP/$path"
    fi
    install -m 0644 "$REPO/$path" "$ROOT/$path.battery-new"
    mv -T -- "$ROOT/$path.battery-new" "$ROOT/$path"
done
install -D -m 0644 "$REPO/systemd/hpr-gpio-control.service.d/50-battery-soc.conf" "$DROPIN"
systemd-analyze verify /etc/systemd/system/hpr-gpio-control.service
systemctl daemon-reload
systemctl restart hpr-gpio-control.service hpr-trike-config-sync.service
systemctl is-active --quiet hpr-gpio-control.service hpr-trike-config-sync.service
[[ "$config_before" == "$(sha256sum "$CONFIG")" ]]
[[ "$before" == "$(fingerprint)" ]]
printf 'Battery estimator capability installed. Central profile activation remains required. Backup %s\n' "$BACKUP"

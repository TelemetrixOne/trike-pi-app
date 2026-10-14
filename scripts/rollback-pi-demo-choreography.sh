#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
BACKUP="$(realpath "${1:?Supply the demo-choreography backup directory}")"
[[ "$BACKUP" == /opt/hpr/backups/demo-choreography-* && -f "$BACKUP/demo_show.py" && -f "$BACKUP/hpr-demo-sequence.py" ]] || {
  echo 'Not a valid demo choreography backup' >&2; exit 1;
}
cp -a "$BACKUP/demo_show.py" /opt/hpr/gateway/hpr_gateway/services/demo_show.py
cp -a "$BACKUP/hpr-demo-sequence.py" /opt/hpr/gateway/bin/hpr-demo-sequence.py
systemctl restart hpr-demo-show.service
systemctl is-active --quiet hpr-demo-show.service
echo 'Previous choreography restored; no show triggered.'

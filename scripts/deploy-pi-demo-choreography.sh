#!/usr/bin/env bash
# Only update the idle demo listener and its console command. Never run a show.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT=/opt/hpr/gateway
BACKUP="/opt/hpr/backups/demo-choreography-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
python3 - "$INSTALL_ROOT" <<'PY'
from pathlib import Path
import hashlib, sys
root = Path(sys.argv[1])
for relative, expected in {
    'hpr_gateway/services/demo_show.py': '84bee6755c9ab0063fe96801c2a925a2685686789d386da741ef3668c50396db',
    'bin/hpr-demo-sequence.py': 'a2019b15a8c0cbc848c928d1f3890aa4a5359bc762d497f6b8abc10b8ceb7f15',
}.items():
    digest = hashlib.sha256((root / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if digest != expected:
        raise SystemExit(f'Unexpected live {relative}; refusing overwrite')
PY
systemctl is-active --quiet hpr-demo-show.service
# Read-only subscription: do not restart a show that is currently playing.
PYTHONPATH="$INSTALL_ROOT" "$INSTALL_ROOT/venv/bin/python" - <<'PY'
import json, threading, time
from hpr_gateway.config import load_config
from hpr_gateway.mqtt import make_client
cfg = load_config('/etc/hpr/hpr.yaml')
done, states = threading.Event(), []
client = make_client(cfg, f'hpr-{cfg.trike_id}-demo-deployment-{int(time.time())}')
def connected(c, *_args):
    c.subscribe(cfg.topic('control', 'demo', 'status'), qos=1)
def message(_c, _u, m):
    states.append(json.loads(m.payload)['state'])
    done.set()
client.on_connect, client.on_message = connected, message
client.connect(cfg.mqtt.host, cfg.mqtt.port, 15)
client.loop_start()
try:
    if not done.wait(5) or states[-1] not in ('ready', 'completed', 'failed', 'rejected'):
        raise SystemExit('Demo is running or idle state is unverified; refusing restart')
finally:
    client.disconnect()
    client.loop_stop()
PY
"$INSTALL_ROOT/venv/bin/python" -m py_compile "$REPO_ROOT/hpr_gateway/services/demo_show.py" "$REPO_ROOT/bin/hpr-demo-sequence.py"
install -d -m 0700 "$BACKUP"
cp -a "$INSTALL_ROOT/hpr_gateway/services/demo_show.py" "$BACKUP/demo_show.py"
cp -a "$INSTALL_ROOT/bin/hpr-demo-sequence.py" "$BACKUP/hpr-demo-sequence.py"
cp -a /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
systemctl cat hpr-demo-show.service > "$BACKUP/unit.txt"
cp -a "$REPO_ROOT/scripts/rollback-pi-demo-choreography.sh" "$BACKUP/rollback.sh"
restore_on_failure() { bash "$BACKUP/rollback.sh" "$BACKUP"; }
trap restore_on_failure ERR
install -m 0644 "$REPO_ROOT/hpr_gateway/services/demo_show.py" "$INSTALL_ROOT/hpr_gateway/services/demo_show.py"
install -m 0755 "$REPO_ROOT/bin/hpr-demo-sequence.py" "$INSTALL_ROOT/bin/hpr-demo-sequence.py"
systemctl restart hpr-demo-show.service
sleep 2
systemctl is-active --quiet hpr-demo-show.service
cmp /etc/hpr/hpr.yaml "$BACKUP/hpr.yaml"
trap - ERR
echo "Ten-second choreography deployed; only demo listener restarted; no show triggered. Backup: $BACKUP"

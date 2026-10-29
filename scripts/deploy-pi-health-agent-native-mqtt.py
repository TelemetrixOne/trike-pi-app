#!/usr/bin/env python3
"""Preserve and remove only retired metadata from the identified live observer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paho.mqtt.client as mqtt
import yaml
from hpr_gateway.config import load_config
from hpr_gateway.retired_metadata import strip_health_discovery

TARGET = Path('/opt/hpr/trike1-health-agent/health_agent.py')
HEALTH_CONFIG = Path('/etc/hpr/trike1-health-agent.yaml')
HEALTH_ENV = Path('/etc/hpr/trike1-health-agent.env')
UNIT = 'hpr-trike1-health-agent.service'
EXPECTED = '313267400af1653f611786f10781b3ccfc9aae9a5e7707a2b1423898a4878750'


def system(*args):
    return subprocess.check_output(['systemctl', *args], text=True, stderr=subprocess.STDOUT, timeout=30).strip()


def fingerprint():
    return system('show', 'hpr-gpio-control.service', 'hpr-video-compositor.service',
                  'hpr-video-mediamtx.service', '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState')


def snapshot(config, root, timeout=4, after=None, enabled=None):
    values = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='observer-passive-' + str(uuid.uuid4()))
    client.username_pw_set(config.mqtt.username, config.mqtt.password)
    client.on_connect = lambda cl, *_: cl.subscribe([(root + suffix, 1) for suffix in ('/state', '/enabled/state', '/enabled/set')])
    def receive(_cl, _userdata, msg):
        try:
            values[msg.topic] = json.loads(msg.payload) if msg.topic == root + '/state' else msg.payload.decode()
        except (ValueError, UnicodeDecodeError):
            pass
    client.on_message = receive
    client.connect(config.mqtt.host, config.mqtt.port, 30); client.loop_start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            state = values.get(root + '/state', {})
            if after is not None and state.get('enabled') is enabled:
                stamp = datetime.fromisoformat(state['generated_at'].replace('Z', '+00:00')).timestamp()
                if stamp >= after and values.get(root + '/enabled/state') == ('ON' if enabled else 'OFF'):
                    return values
            time.sleep(.1)
    finally:
        client.disconnect(); client.loop_stop()
    if after is not None:
        raise RuntimeError('No fresh matching native observer state; inspect before restoring')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-approved', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.restart_approved:
        raise RuntimeError('Root and explicit health-agent restart approval required')
    raw = TARGET.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise RuntimeError('Unidentified live observer enhancement; preserve and investigate')
    updated = strip_health_discovery(raw)
    config = load_config('/etc/hpr/hpr.yaml')
    local = yaml.safe_load(HEALTH_CONFIG.read_text())
    root = local['mqtt']['topic_root'].rstrip('/')
    prior = snapshot(config, root)
    state = prior.get(root + '/state', {})
    if type(state.get('enabled')) is not bool:
        raise RuntimeError('Missing native observer enabled state')
    stamp = datetime.fromisoformat(state['generated_at'].replace('Z', '+00:00')).timestamp()
    interval = int(local.get('interval_seconds', 60))
    if abs(time.time() - stamp) > interval + 15:
        raise RuntimeError('Stale native observer state')
    enabled = state['enabled']
    if prior.get(root + '/enabled/set') != ('ON' if enabled else 'OFF'):
        raise RuntimeError('Cannot preserve enabled mode through natural retained subscription')
    system('is-active', '--quiet', UNIT)
    before = fingerprint()
    saved_config = Path('/etc/hpr/hpr.yaml').read_bytes()
    backup = Path('/opt/hpr/backups') / ('health-agent-native-mqtt-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(TARGET, backup / 'health_agent.py')
    shutil.copy2(HEALTH_CONFIG, backup / HEALTH_CONFIG.name)
    shutil.copy2(HEALTH_ENV, backup / HEALTH_ENV.name)
    (backup / 'unit.txt').write_text(system('cat', UNIT))
    (backup / 'native-state.json').write_text(json.dumps(prior))
    print('Backup: ' + str(backup), flush=True)
    stat = TARGET.stat()
    fd, temporary = tempfile.mkstemp(dir=TARGET.parent, prefix='.native-health-')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(updated); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary, stat.st_mode & 0o777); os.chown(temporary, stat.st_uid, stat.st_gid)
        if TARGET.read_bytes() != raw:
            raise RuntimeError('Source changed during preflight')
        os.replace(temporary, TARGET)
        started = time.time()
        system('restart', UNIT)
        current = snapshot(config, root, timeout=max(15, interval + 10), after=started, enabled=enabled)
        if fingerprint() != before or Path('/etc/hpr/hpr.yaml').read_bytes() != saved_config:
            raise RuntimeError('Unexpected hardware service/config change')
        if TARGET.read_bytes() != updated or HEALTH_CONFIG.read_bytes() != (backup / HEALTH_CONFIG.name).read_bytes():
            raise RuntimeError('Unexpected observer source/config change')
        print('PASS: fresh native health state; enabled=' + str(enabled) + '; GPIO/video/config unchanged')
        print('installed_sha256=' + hashlib.sha256(updated).hexdigest())
    except Exception:
        print('Observer migration needs inspection. Backup: ' + str(backup), file=sys.stderr)
        raise
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


if __name__ == '__main__': main()

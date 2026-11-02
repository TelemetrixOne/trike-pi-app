#!/usr/bin/env python3
"""Guarded config-writer migration. Never publishes configuration or commands."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import paho.mqtt.client as mqtt
from hpr_gateway.config import load_config
from hpr_gateway.config_transaction import config_lock, MARKER, PENDING
from hpr_gateway.services.trike_config_sync import ProfileApplier, LEGACY_UNITS

ROOT = Path('/opt/hpr/gateway')
CONFIG = Path('/etc/hpr/hpr.yaml')
OWNER = 'hpr-trike-config-sync.service'
BASELINE = {
    'trike': '6553ed2dd389bf164331cc5c970aead1c6953df2b964c21c7cfa2862bbe90ad4',
    'gpio': '1b2e56b31fbe1b64b49505a93a63cb58d9fd266b249e5fc59938982da57526bf',
    'tpms': '86fd378f3e89b0cdc5c20c55898152febb54b7b1bfa5abedc914669d89166f2d',
    'power_cadence': 'f11a521d0e6dd80d35d9caf48b5e7450274c497516e19c148959a5bb8a9981e9',
}
FILES = ['hpr_gateway/config_transaction.py'] + [
    'hpr_gateway/services/' + section + '_config_sync.py' for section in BASELINE]
CRITICAL = ['hpr-gpio-control.service', 'hpr-video-compositor.service',
            'hpr-video-front.service', 'hpr-video-rear.service', 'hpr-video-mediamtx.service']


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=45).strip()


def fingerprint():
    return command('systemctl', 'show', *CRITICAL, '-p', 'Id', '-p', 'MainPID',
                   '-p', 'ActiveState', '-p', 'NRestarts', '-p', 'ExecMainStartTimestamp')


def read_topic(config, suffix, accept, timeout=25):
    """Short-lived passive client: SUBSCRIBE only, no actuator/config publish."""
    result = []
    topic = config.topic('config', 'trike', suffix)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='config-migration-passive-' + str(uuid.uuid4()))
    client.username_pw_set(config.mqtt.username, config.mqtt.password)
    client.on_connect = lambda cl, *_: cl.subscribe(topic, qos=1)
    def receive(_cl, _userdata, msg):
        if len(msg.payload) > 262144:
            return
        try:
            value = json.loads(msg.payload)
            if accept(value, result):
                result.append(value)
        except (ValueError, KeyError, TypeError):
            return
    client.on_message = receive
    client.connect(config.mqtt.host, config.mqtt.port, 30)
    client.loop_start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if result and (suffix == 'desired' or len(result) >= 2):
                return result[-1]
            time.sleep(.1)
    finally:
        client.disconnect(); client.loop_stop()
    raise RuntimeError('No acceptable fresh ' + suffix + ' evidence')


def validate_status(config, expected):
    def accept(value, previous):
        return (value.get('schema_version') == 2 and value.get('config_sync_version') == 2
                and value.get('single_writer') is True and value.get('owner') == 'trike-config-sync'
                and value.get('state') == 'applied' and value.get('hash') == expected
                and len(value.get('section_hashes', {})) == 7
                and abs(time.time() - value['published_at']) < 8
                and (not previous or (value['boot_id'] == previous[-1]['boot_id']
                                      and value['seq'] > previous[-1]['seq'])))
    value = read_topic(config, 'status', accept)
    print('PASS: fresh increasing single-writer v2 applied heartbeats, hash=' + value['hash'])


def baseline():
    if CONFIG.with_name(MARKER).exists() or (ROOT / FILES[0]).exists():
        raise RuntimeError('Existing migration/helper; stop and identify before replacing')
    for section, expected in BASELINE.items():
        raw = (ROOT / ('hpr_gateway/services/' + section + '_config_sync.py')).read_bytes()
        if hashlib.sha256(b'\n'.join(raw.splitlines()) + b'\n').hexdigest() != expected:
            raise RuntimeError('Unidentified live config source: ' + section)
    for name in PENDING:
        if CONFIG.with_name(name).exists():
            raise RuntimeError('Pending transaction needs separate recovery: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-hash', required=True, help='Fresh authoritative full-profile hash from central owner')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Run as root using the installed gateway venv')
    config = load_config(str(CONFIG))
    if args.validate_only:
        validate_status(config, args.expected_hash)
        return
    baseline()
    original = CONFIG.read_bytes()
    profile = read_topic(config, 'desired', lambda value, _: value.get('hash') == args.expected_hash)
    candidate, changed = ProfileApplier(str(CONFIG)).prepare(config, profile)
    if changed:
        raise RuntimeError('Migration would restart hardware services; stop: ' + ', '.join(changed))
    before = fingerprint()
    command('systemctl', 'is-active', '--quiet', OWNER)
    for unit in LEGACY_UNITS:
        dropin = Path('/etc/systemd/system') / (unit + '.d') / '90-config-single-writer.conf'
        if dropin.exists():
            raise RuntimeError('Unidentified existing migration drop-in: ' + str(dropin))
    owner_dropin = Path('/etc/systemd/system') / (OWNER + '.d') / '90-config-single-writer.conf'
    if owner_dropin.exists():
        raise RuntimeError('Unidentified existing owner drop-in: ' + str(owner_dropin))
    backup = Path('/opt/hpr/backups') / ('config-single-writer-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(CONFIG, backup / 'hpr.yaml')
    (backup / 'critical-services.txt').write_text(before)
    (backup / 'desired.json').write_text(json.dumps(profile))
    for name in FILES[1:]:
        target = backup / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    for unit in (OWNER, *LEGACY_UNITS):
        (backup / (unit + '.txt')).write_text(command('systemctl', 'cat', unit))
        state = subprocess.run(['systemctl', 'is-enabled', unit], capture_output=True, text=True, timeout=10)
        (backup / (unit + '.enabled')).write_text(state.stdout)
    print('Backup: ' + str(backup), flush=True)
    try:
        command('systemctl', 'stop', OWNER, *LEGACY_UNITS)
        with config_lock(CONFIG):
            baseline()
            if CONFIG.read_bytes() != original:
                raise RuntimeError('Config changed during preflight; preserve and retry fresh')
            for name in FILES:
                shutil.copyfile(SOURCE / name, ROOT / name)
                os.chmod(ROOT / name, 0o644)
            for unit in LEGACY_UNITS:
                directory = Path('/etc/systemd/system') / (unit + '.d')
                directory.mkdir(exist_ok=True)
                shutil.copyfile(SOURCE / 'scripts/config-single-writer.conf', directory / '90-config-single-writer.conf')
            owner_dropin.parent.mkdir(exist_ok=True)
            shutil.copyfile(SOURCE / 'scripts/config-single-owner.conf', owner_dropin)
            CONFIG.with_name(MARKER).write_text('config_sync_version=2\n')
            command('systemctl', 'disable', *LEGACY_UNITS)
            command('systemctl', 'daemon-reload')
            command('systemctl', 'enable', OWNER)
        command('systemctl', 'start', OWNER)
        validate_status(config, args.expected_hash)
        current = load_config(str(CONFIG))
        old, new = config.data, current.data
        # Only the full-profile hash metadata may be added/updated on startup.
        old.setdefault('configuration', {})['trike_hash'] = args.expected_hash
        if old != new or fingerprint() != before:
            raise RuntimeError('Unexpected config or GPIO/video service change')
        print('PASS: migration complete; hardware config and GPIO/video processes unchanged')
    except Exception:
        # Fail closed: never restart legacy writers against stale retained data.
        subprocess.run(['systemctl', 'stop', OWNER, *LEGACY_UNITS], timeout=45)
        print('Migration incomplete; config writers stopped, hardware services untouched. Backup: ' + str(backup), file=sys.stderr)
        raise


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

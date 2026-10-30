#!/usr/bin/env python3
"""Guarded observer/demo rollout. No GPIO/video source or actuator commands."""
import argparse
import copy
from datetime import datetime, timezone
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
import yaml
from hpr_gateway.config import HprConfig, load_config
from hpr_gateway.config_transaction import config_lock, migrated, PENDING
from hpr_gateway.services.trike_config_sync import atomic_write
from hpr_gateway.mqtt import make_client

ROOT = Path('/opt/hpr/gateway')
CONFIG = Path('/etc/hpr/hpr.yaml')
OWNER = 'hpr-trike-config-sync.service'
LEGACY = 'hpr-trike1-health-agent.service'
NEW = 'hpr-health-agent.service'
AFFECTED = ['hpr-services-health.service', 'hpr-gps-recovery.service',
            'hpr-demo-show.service', 'hpr-heartbeat.service', OWNER]
PROTECTED = ['hpr-gpio-control.service', 'hpr-video-compositor.service',
             'hpr-video-mediamtx.service', 'hpr-heart-rate.service', 'hpr-tpms.service',
             'hpr-power-cadence.service', 'hpr-derailleur.service', 'hpr-gps.service']
NEW_UNIT = Path('/etc/systemd/system') / NEW
DEMO_DROPIN = Path('/etc/systemd/system/hpr-demo-show.service.d/90-durable-replay.conf')
FENCE = Path('/etc/systemd/system') / (LEGACY + '.d/95-canonical-observer.conf')
MARKER = Path('/etc/hpr/.canonical-health-observer-v1')


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=35).strip()


def system(*args):
    return command('systemctl', *args)


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def fingerprint():
    return system('show', *PROTECTED, '-p', 'Id', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts')


def topics(config, wanted, timeout=12):
    values = {}
    client = make_client(config, 'assessment-passive-' + uuid.uuid4().hex)
    client.on_connect = lambda cl, *_: cl.subscribe([(topic, 1) for topic in wanted])
    def receive(_cl, _ud, message):
        if len(message.payload) > 262144:
            return
        try:
            values[message.topic] = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            values[message.topic] = message.payload.decode(errors='replace')
    client.on_message = receive
    client.connect(config.mqtt.host, config.mqtt.port, 30)
    client.loop_start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(.1)
        return values
    finally:
        client.disconnect(); client.loop_stop()


def updated_data(config, legacy):
    if not set(legacy) <= {'mqtt', 'interval_seconds', 'services', 'remediation', 'limits'}:
        raise RuntimeError('Unidentified legacy observer enhancement; preserve and inspect')
    data = copy.deepcopy(config.data)
    if 'health_agent' in data or 'health_agent' in data.get('services', {}):
        raise RuntimeError('Canonical observer configuration already exists; inspect first')
    data['health_agent'] = {'interval_seconds': legacy['interval_seconds'],
                            'units': legacy['services'], 'limits': legacy['limits']}
    data['services']['health_agent'] = {'enabled': True}
    data['services']['heartbeat'].update(publish_legacy_topics=False, legacy_topics=[])
    for name in ('gpio_config_sync', 'tpms_config_sync', 'power_cadence_config_sync'):
        data['services'][name]['enabled'] = False
    # Scoped hash metadata remains archived in the protected YAML backup.
    for name in ('gpio_hash', 'tpms_hash', 'power_cadence_hash'):
        data['configuration'].pop(name, None)
    HprConfig(data, str(CONFIG))
    return data


def validate(config, after, expected_hash):
    base = config.resolve_topic(config.get('health.base_topic'), 'pi')
    keys = [base + '/' + key for key in ('system_metrics', 'tpms_detail', 'video_detail')]
    keys += [config.topic('agents', 'health', 'state'), config.topic('control', 'demo', 'status'),
             config.topic('nav', 'recovery'), config.topic('config', 'trike', 'status')]
    values = topics(config, keys, 18)
    def fresh(value, key):
        stamp = value.get(key)
        return stamp and datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() >= after
    metrics, tpms, video, observer, demo, gps, sync = [values.get(key, {}) for key in keys]
    checks = {
        'metrics_fresh_python_severity': fresh(metrics, 'observed_at') and metrics.get('logs_complete') is True and
            metrics.get('logs_severity_source') == 'journal_priority_and_recognised_python_levels',
        'tpms_applied_assignment': fresh(tpms, 'observed_at') and tpms.get('assignment_source') == 'applied_trike_configuration' and len(tpms.get('sensors', [])) == 3,
        'video_frame_progress': fresh(video, 'observed_at') and video.get('overall') == 'Good',
        'canonical_observer': fresh(observer, 'generated_at') and observer.get('schema') == 'hpr.health_agent.v1' and observer.get('remediations') == [] and observer.get('remediation_owner') == 'systemd_and_dedicated_recovery',
        'demo_ready_no_request_sent': fresh(demo, 'at') and demo.get('state') == 'ready',
        'gps_observer_fresh': fresh(gps, 'observed_at') and gps.get('receiver_active') is True and gps.get('state') in ('healthy', 'no_fix', 'acquiring'),
        'single_owner_profile_preserved': sync.get('schema_version') == 2 and sync.get('single_writer') is True and sync.get('hash') == expected_hash and sync.get('state') == 'applied',
    }
    print(json.dumps({'validation': checks}), flush=True)
    if not all(checks.values()):
        raise RuntimeError('Passive evidence incomplete; inspect protected backup and logs')
    return values


def main():
    import pwd
    import grp
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--observer-restarts-approved', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.observer_restarts_approved:
        raise RuntimeError('Root and approved affected observer restarts required')
    raw = CONFIG.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_config_sha256:
        raise RuntimeError('Live YAML changed; repeat comparison')
    if not migrated(CONFIG) or any(CONFIG.with_name(name).exists() for name in PENDING):
        raise RuntimeError('Config owner not migrated or recovery pending')
    manifest = json.loads((SOURCE / 'scripts/assessment-health-baseline.json').read_text())
    for name, expected in manifest['baseline'].items():
        target = ROOT / name
        if not target.is_file() or digest(target) != expected:
            raise RuntimeError('Unidentified live source: ' + name)
    for name in manifest['install']:
        if name not in manifest['baseline'] and (ROOT / name).exists():
            raise RuntimeError('Unexpected new target exists: ' + name)
        if not (SOURCE / name).is_file():
            raise RuntimeError('Package incomplete: ' + name)
    for target in (NEW_UNIT, DEMO_DROPIN, FENCE, MARKER):
        if target.exists():
            raise RuntimeError('Migration target already exists: ' + str(target))
    legacy_source = Path('/opt/hpr/trike1-health-agent/health_agent.py')
    if digest(legacy_source) != '87ec6a145243d1ab6df50b391839fee0f468112a845d1eee7e60d8ca6e5019fd':
        raise RuntimeError('Legacy observer changed; preserve and investigate')
    config = load_config(str(CONFIG))
    legacy_config = Path('/etc/hpr/trike1-health-agent.yaml')
    legacy = yaml.safe_load(legacy_config.read_text())
    candidate = updated_data(config, legacy)
    mode_topic = config.topic('agents', 'health', 'enabled', 'state')
    mode = topics(config, [mode_topic], 3).get(mode_topic)
    if mode not in ('ON', 'OFF'):
        raise RuntimeError('No known observer mode; do not assume')
    for unit in AFFECTED + [LEGACY]:
        system('is-active', '--quiet', unit)
    before = fingerprint()
    backup = Path('/opt/hpr/backups') / ('assessment-health-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    saved = []
    paths = [CONFIG, legacy_source, legacy_config, Path('/etc/hpr/trike1-health-agent.env')]
    paths += [ROOT / name for name in manifest['install']]
    paths += [Path('/etc/systemd/system') / unit for unit in AFFECTED + [LEGACY]]
    for path in paths:
        relative = str(path).lstrip('/')
        exists = path.exists()
        saved.append({'path': str(path), 'existed': exists})
        if exists:
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for unit in AFFECTED + [LEGACY]:
        (backup / (unit + '.txt')).write_text(system('cat', unit))
    (backup / 'manifest.json').write_text(json.dumps(saved, indent=2))
    (backup / 'before.txt').write_text(before)
    print('Protected backup: ' + str(backup), flush=True)
    # Reject unknown durable state before stopping any service.
    for directory in (Path('/var/lib/hpr-health-agent'), Path('/var/lib/hpr-demo-show')):
        if directory.exists() and any(directory.iterdir()):
            raise RuntimeError('Existing unrecognised state; preserve and inspect')
    try:
        system('stop', LEGACY, *AFFECTED)
        with config_lock(CONFIG):
            if CONFIG.read_bytes() != raw or any(CONFIG.with_name(name).exists() for name in PENDING):
                raise RuntimeError('Config changed during preflight')
            for name in manifest['install']:
                target = ROOT / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SOURCE / name, target)
                os.chmod(target, 0o755 if name.startswith('bin/') else 0o644)
            user = config.get('system.service_user', 'admin')
            group = config.get('system.service_group', user)
            uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid
            for directory in (Path('/var/lib/hpr-health-agent'), Path('/var/lib/hpr-demo-show')):
                directory.mkdir(mode=0o700, exist_ok=True)
                os.chown(directory, uid, gid); os.chmod(directory, 0o700)
            mode_path = Path('/var/lib/hpr-health-agent/mode.json')
            mode_path.write_text(json.dumps({'enabled': mode == 'ON'}))
            os.chown(mode_path, uid, gid); os.chmod(mode_path, 0o600)
            NEW_UNIT.write_text(f'''[Unit]
Description=HPR canonical process/resource observer (no remediation authority)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH={ROOT}
StateDirectory=hpr-health-agent
StateDirectoryMode=0700
ExecStart={ROOT}/venv/bin/python -m hpr_gateway.services.health_agent --config {CONFIG}
Restart=always
RestartSec=5
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
''')
            DEMO_DROPIN.parent.mkdir(exist_ok=True)
            DEMO_DROPIN.write_text('[Service]\nStateDirectory=hpr-demo-show\nStateDirectoryMode=0700\n')
            FENCE.parent.mkdir(exist_ok=True)
            FENCE.write_text('[Unit]\nConditionPathExists=!/etc/hpr/.canonical-health-observer-v1\n')
            MARKER.write_text('Dedicated recovery owners; canonical health observer only.\n')
            atomic_write(CONFIG, yaml.safe_dump(candidate, sort_keys=False).encode())
        command(str(ROOT / 'venv/bin/python'), '-B', '-m', 'hpr_gateway.validate_config', '--config', str(CONFIG))
        system('daemon-reload')
        command('systemd-analyze', 'verify', str(NEW_UNIT), '/etc/systemd/system/hpr-demo-show.service')
        system('disable', LEGACY)
        system('enable', NEW)
        after = time.time()
        system('start', NEW, *AFFECTED)
        for unit in AFFECTED + [NEW]:
            system('is-active', '--quiet', unit)
        values = validate(load_config(str(CONFIG)), after, config.get('configuration.trike_hash'))
        if fingerprint() != before:
            raise RuntimeError('Protected hardware service fingerprint changed')
        if load_config(str(CONFIG)).data != candidate:
            raise RuntimeError('Configuration diverged; do not overwrite central changes')
        (backup / 'validated.json').write_text(json.dumps(values, indent=2))
        print(json.dumps({'deployed': True, 'backup': str(backup), 'protected_services_unchanged': True,
                          'restarted': AFFECTED, 'legacy_observer_retired': LEGACY, 'new_observer': NEW,
                          'gpio_credential_reload_deployed': False}), flush=True)
    except Exception:
        system('stop', NEW, *AFFECTED)
        with config_lock(CONFIG):
            current = load_config(str(CONFIG)).data
            if current not in (config.data, candidate):
                print('Config changed externally. Observers stopped; manual reviewed rollback required: ' + str(backup), flush=True)
                raise
            written_paths = {CONFIG, *(ROOT / name for name in manifest['install'])}
            for item in saved:
                path = Path(item['path'])
                # Only restore files this deployment writes; legacy source/env
                # and unchanged units are backups, never replacement targets.
                if path not in written_paths:
                    continue
                if item['existed']:
                    shutil.copy2(backup / str(path).lstrip('/'), path)
                elif path.exists():
                    path.unlink()
            subprocess.run(['systemctl', 'disable', NEW], check=False, capture_output=True, timeout=20)
            for path in (NEW_UNIT, DEMO_DROPIN, FENCE, MARKER):
                if path.exists(): path.unlink()
        system('daemon-reload')
        system('enable', LEGACY)
        system('start', LEGACY, *AFFECTED)
        print('Observer changes rolled back; backup retained at ' + str(backup), flush=True)
        raise


if __name__ == '__main__': main()

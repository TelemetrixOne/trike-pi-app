#!/usr/bin/env python3
"""Stationary-only isolated runtime activation and GPIO credential-reload hook.

No actuator commands are sent. GPIO restarts once during normal activation.
The original environment/source remain available for reviewed rollback.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from hpr_gateway.config import load_config
from hpr_gateway.config_transaction import config_lock, PENDING

ROOT = Path('/opt/hpr/gateway')
CONFIG = Path('/etc/hpr/hpr.yaml')
VENV = ROOT / 'venv'
GPIO = 'hpr-gpio-control.service'
OWNER = 'hpr-trike-config-sync.service'
WORKERS = ['hpr-bluetooth-recovery.service', 'hpr-demo-show.service', 'hpr-derailleur.service',
    GPIO, 'hpr-gps-recovery.service', 'hpr-gps.service', 'hpr-health-agent.service',
    'hpr-heart-rate.service', 'hpr-heartbeat.service', 'hpr-pi-power-health.service',
    'hpr-power-cadence.service', 'hpr-power-watch.service', 'hpr-services-health.service',
    'hpr-tpms.service', OWNER]
VIDEO = ['hpr-video-compositor.service', 'hpr-video-mediamtx.service']
INSTALL = ['hpr_gateway/services/gpio_control.py', 'hpr_gateway/credential_reload.py']


def run(*args, timeout=40):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def system(*args):
    return run('systemctl', *args)


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def fingerprint(units):
    return system('show', *units, '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState')


def active_workers():
    values = json.loads(system('list-units', '--type=service', '--state=active', '--output=json', '--no-pager'))
    return sorted(v['unit'] for v in values if str(VENV / 'bin/python') in system('show', v['unit'], '-p', 'ExecStart', '--value'))


def evidence(config, timeout=6):
    spec = importlib.util.spec_from_file_location('observer_deployment', SOURCE / 'scripts/deploy-pi-assessment-health.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    base = config.topic('control')
    topics = [base + '/' + suffix for suffix in ('headlight/state', 'headlight/mode', 'aux_lights/state',
              'horn/state', 'drs/mode', 'drs/state', 'drs/status')]
    topics.append(config.resolve_topic(config.get('health.base_topic'), 'pi') + '/gpio_evidence')
    return module.topics(config, topics, timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--test-unit', required=True)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--stationary-restart-approved', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.stationary_restart_approved:
        raise RuntimeError('Root and explicit stationary maintenance approval required')
    candidate = args.candidate.resolve()
    if candidate.parent != Path('/opt/hpr/candidates') or not candidate.name.startswith('runtime-'):
        raise RuntimeError('Candidate must be a reviewed /opt/hpr/candidates/runtime-* environment')
    if not re.fullmatch(r'hpr-environment-tests-[A-Za-z0-9.-]+', args.test_unit):
        raise RuntimeError('Unexpected test unit name')
    if system('show', args.test_unit, '-p', 'Result', '--value') != 'success' or system('show', args.test_unit, '-p', 'ExecMainStatus', '--value') != '0':
        raise RuntimeError('Candidate sandbox test run did not pass')
    test_log = run('journalctl', '-u', args.test_unit, '-o', 'cat', '--no-pager')
    if not re.search(r'Ran 195 tests in ', test_log) or '\nOK\n' not in test_log:
        raise RuntimeError('Missing complete candidate test evidence')
    if 'include-system-site-packages = false' not in (candidate / 'pyvenv.cfg').read_text():
        raise RuntimeError('Candidate inherits system packages')
    if VENV.is_symlink() or not VENV.is_dir():
        raise RuntimeError('Unexpected active environment layout; preserve and inspect')
    raw = CONFIG.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_config_sha256 or any(CONFIG.with_name(p).exists() for p in PENDING):
        raise RuntimeError('Configuration changed or recovery pending')
    expected = json.loads((SOURCE / 'scripts/isolated-runtime-baseline.json').read_text())
    for name, value in expected.items():
        path = ROOT / name
        if value is None and not path.exists(): continue
        if path.is_symlink() or not path.is_file() or digest(path) != value:
            raise RuntimeError('Unidentified runtime source: ' + name)
    native = json.loads((candidate / 'native-bindings.json').read_text())
    if set(native) != {'gps', 'lgpio', '_lgpio'}:
        raise RuntimeError('Incomplete audited native bindings')
    for value in native.values():
        path = Path(value['source'])
        if not path.resolve().is_relative_to('/usr/lib') or hashlib.sha256(path.read_bytes()).hexdigest() != value['sha256']:
            raise RuntimeError('OS native binding changed after candidate build')
    run(str(candidate / 'bin/python'), '-I', '-m', 'pip', 'check')
    pins = dict(line.split('==') for line in (SOURCE / 'requirements.lock').read_text().splitlines()
                if '==' in line and not line.startswith('#'))
    check = 'import importlib.metadata,json;expected=' + repr(pins) + ';actual={k:importlib.metadata.version(k) for k in expected};assert actual==expected,(actual,expected)'
    run(str(candidate / 'bin/python'), '-I', '-c', check)
    if active_workers() != sorted(WORKERS):
        raise RuntimeError('Unexpected active Python workers; preserve and inspect')
    config = load_config(str(CONFIG))
    video_before = fingerprint(VIDEO)
    states_before = evidence(config)
    backup = Path('/opt/hpr/backups') / ('isolated-runtime-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(CONFIG, backup / 'hpr.yaml')
    (backup / 'before-states.json').write_text(json.dumps(states_before, indent=2))
    (backup / 'before-units.txt').write_text(fingerprint(WORKERS + VIDEO))
    (backup / 'candidate-tests.txt').write_text(test_log)
    for name in INSTALL:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / name).exists(): shutil.copy2(ROOT / name, target)
    (backup / 'manifest.json').write_text(json.dumps({'candidate': str(candidate), 'workers': WORKERS,
        'source_baseline': {name: expected[name] for name in INSTALL}, 'configuration_sha256': args.expected_config_sha256}, indent=2))
    # Candidate contains public dependencies only. Make the isolated tree readable
    # to service users, without following native-binding symlinks into the OS.
    os.chmod(candidate.parent, 0o755)
    for folder, directories, files in os.walk(candidate, followlinks=False):
        os.chmod(folder, 0o755)
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink(): os.chmod(path, 0o755 if path.stat().st_mode & 0o111 else 0o644)
    print('Protected rollback environment and evidence: ' + str(backup), flush=True)
    switched = False
    moved = False
    try:
        system('stop', *WORKERS)
        with config_lock(CONFIG):
            if CONFIG.read_bytes() != raw or any(CONFIG.with_name(p).exists() for p in PENDING):
                raise RuntimeError('Configuration changed during maintenance preflight')
            for name in INSTALL:
                shutil.copyfile(SOURCE / name, ROOT / name)
                os.chmod(ROOT / name, 0o644)
            VENV.rename(backup / 'venv')
            moved = True
            VENV.symlink_to(candidate, target_is_directory=True)
            switched = True
        # One normal GPIO restart. No test commands or restored mode publications.
        system('start', GPIO)
        system('start', *[u for u in WORKERS if u not in (GPIO, OWNER)])
        system('start', OWNER)
        for unit in WORKERS:
            system('is-active', '--quiet', unit)
        states_after = evidence(config, 12)
        gpio_topic = config.resolve_topic(config.get('health.base_topic'), 'pi') + '/gpio_evidence'
        gpio = states_after.get(gpio_topic, {})
        if gpio.get('device') != 'online' or gpio.get('errors') != []:
            raise RuntimeError('GPIO readback evidence failed; inspect protected states')
        if fingerprint(VIDEO) != video_before or CONFIG.read_bytes() != raw:
            raise RuntimeError('Protected video/config state changed during activation')
        (backup / 'after-states.json').write_text(json.dumps(states_after, indent=2))
        (backup / 'after-units.txt').write_text(fingerprint(WORKERS + VIDEO))
        run(str(VENV / 'bin/python'), '-I', '-m', 'pip', 'check')
        print(json.dumps({'activated': str(candidate), 'source_hook_installed': True,
            'gpio_restarts': 1, 'workers_restarted': WORKERS, 'video_unchanged': True,
            'configuration_unchanged': True, 'actuator_commands_sent': 0, 'backup': str(backup)}), flush=True)
    except Exception:
        system('stop', *WORKERS)
        with config_lock(CONFIG):
            if moved:
                if switched and (not VENV.is_symlink() or VENV.resolve() != candidate):
                    raise RuntimeError('Environment changed externally; preserve backup for reviewed rollback')
                if VENV.is_symlink(): VENV.unlink()
                (backup / 'venv').rename(VENV)
            for name in INSTALL:
                if expected[name] is None:
                    (ROOT / name).unlink(missing_ok=True)
                else:
                    shutil.copy2(backup / name, ROOT / name)
        system('start', *WORKERS)
        print('Activation failed; original environment restored. See ' + str(backup), flush=True)
        raise


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

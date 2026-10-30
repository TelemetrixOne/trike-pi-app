#!/usr/bin/env python3
"""Reload the identified stale Bluetooth config reader without restarting HRM."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/opt/hpr/gateway')
from hpr_gateway.config import load_config
from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.services.bluetooth_recovery import adapter_is_up, kernel_scan_failed

ROOT = Path('/opt/hpr/gateway')
UNIT = 'hpr-bluetooth-recovery.service'
PROTECTED = ['hpr-heart-rate.service', 'hpr-gpio-control.service', 'hpr-tpms.service',
             'hpr-power-cadence.service', 'hpr-derailleur.service', 'hpr-gps.service',
             'hpr-video-compositor.service', 'hpr-video-mediamtx.service']


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=25).strip()


def fingerprint():
    return run('systemctl', 'show', *PROTECTED, '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-approved', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.restart_approved:
        raise RuntimeError('Root and affected-monitor restart approval required')
    expected = {'hpr_gateway/config.py': '0cfaecbdbf50057073439d28cb15032f6ec63b81569090a0117e6a41ba8dd64f',
                'hpr_gateway/services/bluetooth_recovery.py': '6a9ebac099b67aa9d17357a91b0d8bf7ef04590e3b360acd50fbaf68d366199b'}
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != digest:
            raise RuntimeError('Reader source changed; inspect before reload')
    config = load_config('/etc/hpr/hpr.yaml')
    adapter = resolve_bluetooth_role(config, 'heart_rate', default_adapter='hci1')
    telemetry = resolve_bluetooth_role(config, 'telemetry', default_adapter='hci0')
    if adapter == telemetry or not all(adapter_is_up(value) for value in (adapter, telemetry)):
        raise RuntimeError('Adapters not both healthy/distinct; inspect without resetting')
    if kernel_scan_failed(adapter, time.time() - 30):
        raise RuntimeError('Recent scan-controller faults require separate investigation')
    text = run('journalctl', '-u', UNIT, '--since=-2min', '-n', '20', '-o', 'cat', '--no-pager')
    if 'missing required config value: network.home_assistant.host' not in text:
        raise RuntimeError('The identified stale-loader symptom is no longer present')
    before = fingerprint()
    backup = Path('/opt/hpr/backups') / ('stale-reader-reload-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    (backup / 'unit.txt').write_text(run('systemctl', 'cat', UNIT))
    (backup / 'before.txt').write_text(before)
    start = time.time()
    run('systemctl', 'restart', UNIT)
    time.sleep(22)
    run('systemctl', 'is-active', '--quiet', UNIT)
    recent = run('journalctl', '-u', UNIT, '--since', '@' + str(start), '-o', 'cat', '--no-pager')
    (backup / 'after-log.txt').write_text(recent)
    if 'missing required config value' in recent or 'ERROR' in recent or 'WARNING' in recent:
        raise RuntimeError('Monitor still reports faults; see protected evidence')
    if fingerprint() != before or not all(adapter_is_up(value) for value in (adapter, telemetry)):
        raise RuntimeError('Protected process or adapter state changed; inspect evidence')
    print(json.dumps({'restarted': UNIT, 'protected_pids_unchanged': True, 'both_adapters_up': True,
                      'stale_loader_errors_after_reload': 0, 'backup': str(backup)}))


if __name__ == '__main__': main()

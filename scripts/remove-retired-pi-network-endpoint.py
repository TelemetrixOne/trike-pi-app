#!/usr/bin/env python3
"""Remove an approved inert endpoint block with single-writer coordination."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, '/opt/hpr/gateway')
import yaml
from hpr_gateway.config import HprConfig, load_config
from hpr_gateway.config_transaction import config_lock, migrated, PENDING
from hpr_gateway.services.trike_config_sync import atomic_write

CONFIG = Path('/etc/hpr/hpr.yaml')
OWNER = 'hpr-trike-config-sync.service'


def system(*args):
    return subprocess.check_output(['systemctl', *args], text=True, stderr=subprocess.STDOUT, timeout=30).strip()


def fingerprints():
    return system('show', 'hpr-gpio-control.service', 'hpr-video-compositor.service',
                  'hpr-video-mediamtx.service', 'hpr-trike1-health-agent.service',
                  '-p', 'Id', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts')


def candidate(config, section):
    if section in {'mqtt', 'mediamtx', 'video', 'tailscale_required'}:
        raise ValueError('Active native network setting cannot be removed')
    result = copy.deepcopy(config.data)
    value = result.get('network', {}).get(section)
    if not isinstance(value, dict) or not {'host', 'port'} <= value.keys() or not value.keys() <= {'host', 'port', 'url', 'username', 'password'}:
        raise ValueError('Unknown endpoint shape; preserve and investigate')
    del result['network'][section]
    HprConfig(result, str(CONFIG))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--section', required=True)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--config-sync-restart-approved', action='store_true')
    args = parser.parse_args()
    import os
    if os.geteuid() != 0 or not args.config_sync_restart_approved:
        raise RuntimeError('Root and explicit config-sync restart approval required')
    raw = CONFIG.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_config_sha256:
        raise RuntimeError('Config changed; take a fresh approved snapshot')
    if not migrated(CONFIG) or any(CONFIG.with_name(name).exists() for name in PENDING):
        raise RuntimeError('Single-writer ownership not ready, or recovery pending')
    config = load_config(str(CONFIG))
    updated = candidate(config, args.section)
    expected_hash = config.get('configuration.trike_hash')
    if not expected_hash:
        raise RuntimeError('No applied central profile hash')
    system('is-active', '--quiet', OWNER)
    before = fingerprints()
    backup = Path('/opt/hpr/backups') / ('retired-endpoint-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(CONFIG, backup / 'hpr.yaml')
    (backup / 'unit.txt').write_text(system('cat', OWNER))
    print('Backup: ' + str(backup), flush=True)
    stopped = False
    try:
        system('stop', OWNER); stopped = True
        with config_lock(CONFIG):
            if CONFIG.read_bytes() != raw or any(CONFIG.with_name(name).exists() for name in PENDING):
                raise RuntimeError('Config or recovery changed during preflight')
            atomic_write(CONFIG, yaml.safe_dump(updated, sort_keys=False).encode())
        system('start', OWNER); stopped = False
        validator = Path(__file__).with_name('update-pi-config-single-writer.py')
        subprocess.run([sys.executable, str(validator), '--validate-only', '--expected-hash', expected_hash], check=True, timeout=40)
        if load_config(str(CONFIG)).data != updated or fingerprints() != before:
            raise RuntimeError('Unexpected configuration or hardware-process change')
        print('PASS: only approved endpoint block removed; fresh applied central hash unchanged; GPIO/video/health processes unchanged')
    except Exception:
        # Never roll back over a newer centrally applied configuration.
        if stopped:
            print('Config writer remains stopped; inspect private backup before recovery', file=sys.stderr)
        print('Endpoint migration needs inspection. Backup: ' + str(backup), file=sys.stderr)
        raise


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

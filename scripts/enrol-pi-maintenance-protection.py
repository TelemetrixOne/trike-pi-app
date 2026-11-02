#!/usr/bin/env python3
"""Enrol guard-only files/config under fresh HTTPS idle admission; no restarts.

First-enrolment bootstrap: the explicitly supplied trusted URL is checked before
operational writes. An existing URL cannot be changed by this entrypoint.
"""
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

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import yaml
from hpr_gateway import maintenance as guard
from hpr_gateway.config_transaction import config_lock, PENDING
from hpr_gateway.services.trike_config_sync import atomic_write

ROOT = Path('/opt/hpr/gateway')
CONFIG = Path('/etc/hpr/hpr.yaml')
BASELINES = {
    'bin/hpr-demo-sequence.py': '484d14f1fd20833105f32fef1120342ccece9d256f01228f9b23585902065235',
    'scripts/deploy-pi-gps-uplift.sh': 'f6a414bea31dbeca6aac986a0d3d72dc20035a6dcd4eda6e8ad0cc7b49c6f841',
    # These two live copies exactly match repository commit 8a6d1a3.
    'scripts/install-pi-gateway.sh': '5dbbff60a6db8b1916ba3d0ddbe1dabac512480b85745deb05ce3dd608ef7d1d',
    'scripts/rebuild-pi-gateway.sh': '6574c6be1312d83ac279c676346888448981c577454f7a89cd7bbb975b628e2d',
}
HELPERS = ('hpr_gateway/maintenance.py', 'scripts/pi-maintenance-check.py',
           'scripts/pi-maintenance-guard.sh')


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def processes():
    return subprocess.check_output(['systemctl', 'show', 'hpr-*.service', '-p', 'Id',
        '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts'], text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protection-url', required=True, help='explicit trusted HTTPS enrolment URL')
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    from urllib.parse import urlsplit
    url = urlsplit(args.protection_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise RuntimeError('Explicit trusted HTTPS URL required')
    initial = CONFIG.read_bytes()
    if hashlib.sha256(initial).hexdigest() != args.expected_config_sha256:
        raise RuntimeError('Live config changed since read-only comparison; preserve and inspect')
    data = yaml.safe_load(initial)
    identity = {k: data['hpr'][k] for k in ('trike_id', 'topic_root')}
    existing = data.get('network', {}).get('maintenance', {})
    if existing and existing.get('protection_url') != args.protection_url:
        raise RuntimeError('Existing maintenance enrolment differs; no URL replacement allowed')
    if any(CONFIG.with_name(name).exists() for name in PENDING):
        raise RuntimeError('Pending configuration recovery; preserve and review')
    def bootstrap_check():
        _, allowed = guard.decision(guard.fetch(args.protection_url), identity, None, datetime.now(timezone.utc))
        if not allowed: raise guard.MaintenanceBlocked('central session not idle')
    # Same local flock as installed guard. Bootstrap supplies only the fresh
    # authority check because the URL is not yet enrolled in the local YAML.
    with guard.admission(checker=bootstrap_check):
        files = list(BASELINES) + list(HELPERS)
        for relative in files:
            live, source = ROOT/relative, SOURCE/relative
            if live.is_symlink() or not source.is_file():
                raise RuntimeError('Unexpected symlink/missing source: '+relative)
            if live.exists() and digest(live) not in (BASELINES.get(relative), digest(source)):
                raise RuntimeError('Unidentified live enhancement; preserve: '+relative)
            if relative in BASELINES and not live.exists():
                raise RuntimeError('Expected existing operator script missing: '+relative)
        before = processes()
        units = {str(p): digest(p) for p in Path('/etc/systemd/system').rglob('*') if p.is_file()
                 and (p.name.startswith('hpr-') or any(parent.name.startswith('hpr-') for parent in p.parents))}
        config_expected = copy.deepcopy(data)
        config_expected.setdefault('network', {}).setdefault('maintenance', {})['protection_url'] = args.protection_url
        backup = Path('/opt/hpr/backups')/('maintenance-guard-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        bootstrap_check()
        backup.mkdir(mode=0o700)
        shutil.copy2(CONFIG, backup/'hpr.yaml')
        manifest = []
        for relative in files:
            live = ROOT/relative
            manifest.append({'path': relative, 'existed': live.exists()})
            if live.exists():
                target = backup/relative; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, target)
        (backup/'before.json').write_text(json.dumps({'processes': before, 'units': units, 'files': manifest}, indent=2))
        # No service operations. Unknown files are neither removed nor replaced.
        for relative in files:
            bootstrap_check()
            live = ROOT/relative
            live.parent.mkdir(parents=True, exist_ok=True)
            temporary = live.with_name(live.name+'.maintenance-new')
            if temporary.exists(): raise RuntimeError('Unexpected pending source file; preserve')
            shutil.copyfile(SOURCE/relative, temporary)
            os.chmod(temporary, 0o644 if relative.endswith('.py') and relative.startswith('hpr_gateway/') else 0o755)
            temporary.replace(live)
        bootstrap_check()
        with config_lock(CONFIG):
            if CONFIG.read_bytes() != initial:
                raise RuntimeError('Config changed externally; leave evidence and do not overwrite')
            bootstrap_check()
            atomic_write(CONFIG, yaml.safe_dump(config_expected, sort_keys=False).encode())
        guard.check()
        if yaml.safe_load(CONFIG.read_text()) != config_expected:
            raise RuntimeError('Read-back mismatch')
        if processes() != before or any(digest(Path(p)) != h for p,h in units.items()):
            raise RuntimeError('Unexpected process/unit change; preserve evidence for review')
        result = {'source_commit': args.source_commit, 'installed_files': files, 'backup': str(backup),
                  'fresh_idle_admission': True, 'services_restarted': [], 'units_and_processes_unchanged': True}
        (backup/'validated.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))


if __name__ == '__main__': main()

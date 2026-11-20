#!/usr/bin/env python3
"""Scoped SOC upgrade; operator approval required, never admitted mid-session."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/opt/hpr/gateway')
sys.path.insert(0, str(ROOT))
from hpr_gateway.maintenance import admission, trusted_config, fetch, decision, MaintenanceBlocked

BASELINE = {
    'hpr_gateway/battery_soc.py': 'ad053508f6cf5d688d3630fc3c732fd5c2fd518d711a078ea404a60f9fbad0a2',
    'hpr_gateway/services/gpio_control.py': 'e1a255e304aff0cf51cdadbce3f9c925c27698161586ec4efd0183d64da5d42c',
}


def approved_phase(payload, identity, prestart):
    _, idle = decision(payload, identity, None, datetime.now(timezone.utc))
    if idle or (prestart and payload.get('phase') == 'scheduled'):
        return
    raise MaintenanceBlocked('SOC repair requires idle state or explicit stationary pre-start approval; active sessions are refused')


def fingerprint():
    text = subprocess.check_output(['systemctl', 'show', 'hpr-*.service', '-p', 'Id', '-p', 'MainPID'], text=True)
    return sorted(block for block in text.split('\n\n') if block.strip() and 'Id=hpr-gpio-control.service' not in block)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stationary-restart-approved', action='store_true', required=True)
    parser.add_argument('--stationary-prestart-approved', action='store_true',
                        help='Explicit operator exception for this battery repair before an armed race; never for an active race')
    parser.add_argument('--defer-restart-to-profile-activation', action='store_true',
                        help='Stage code first; the subsequent supported central profile change performs the single GPIO restart')
    args = parser.parse_args()
    identity, url = trusted_config()
    def preflight():
        approved_phase(fetch(url), identity, args.stationary_prestart_approved)
    source = Path(__file__).resolve().parents[1]
    with admission(checker=preflight):
        incoming = {}
        originals = {}
        for name, expected in BASELINE.items():
            live = ROOT/name
            if live.is_symlink():
                raise RuntimeError('Refusing symlink: '+name)
            old, new = live.read_bytes(), (source/name).read_bytes()
            if hashlib.sha256(old).hexdigest() != expected and old != new:
                raise RuntimeError('Unexpected live source; preserve and review: '+name)
            compile(new, name, 'exec')
            incoming[name], originals[name] = new, old
        config = Path('/etc/hpr/hpr.yaml').read_bytes()
        before = fingerprint()
        backup = Path('/opt/hpr/backups')/('battery-calibration-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        backup.mkdir(mode=0o700)
        for name, old in originals.items():
            dest = backup/name;dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(old);dest.chmod(0o600)
        checkpoint = Path('/var/lib/hpr-battery-soc/estimate.json')
        if checkpoint.exists():
            shutil.copy2(checkpoint, backup/'estimate.json')
        preflight()
        try:
            for name, new in incoming.items():
                target = ROOT/name;temporary = target.with_suffix('.soc-new')
                temporary.write_bytes(new);temporary.chmod(0o644);temporary.replace(target)
            preflight()
            if not args.defer_restart_to_profile_activation:
                subprocess.run(['systemctl', 'restart', 'hpr-gpio-control'], check=True)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'hpr-gpio-control'], check=True)
            assert Path('/etc/hpr/hpr.yaml').read_bytes() == config, 'Configuration changed during code deployment'
            assert fingerprint() == before, 'An unrelated service changed during deployment'
        except Exception:
            for name, old in originals.items():
                (ROOT/name).write_bytes(old)
            preflight()
            subprocess.run(['systemctl', 'restart', 'hpr-gpio-control'], check=True)
            raise
        receipt = dict(backup=str(backup), files=list(incoming), restarted=[] if args.defer_restart_to_profile_activation else ['hpr-gpio-control'],
                       activation_pending=args.defer_restart_to_profile_activation,
                       configuration_unchanged=True, unrelated_services_unchanged=True,
                       operator_prestart_approval=args.stationary_prestart_approved)
        (backup/'receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()

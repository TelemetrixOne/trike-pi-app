#!/usr/bin/env python3
"""Non-actuating acceptance: real idle/lock checks plus isolated denial fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, '/opt/hpr/gateway')
from hpr_gateway import maintenance as guard


def fingerprint():
    return subprocess.check_output(['systemctl', 'show', 'hpr-*.service', '-p', 'Id',
        '-p', 'MainPID', '-p', 'ActiveState', '-p', 'NRestarts'], text=True)


def main():
    # This is a bounded validation, admitted by the real production authority.
    with guard.admission():
        before = fingerprint()
        config_hash = hashlib.sha256(guard.CONFIG.read_bytes()).hexdigest()
        identity, url = guard.trusted_config()
        command = [sys.executable, '/opt/hpr/gateway/scripts/pi-maintenance-check.py']
        # Child does NOT inherit this open-file description: real competing lock.
        denied = subprocess.run(command, capture_output=True, text=True, timeout=12)
        if denied.returncode != 75 or 'another local maintenance operation' not in denied.stderr:
            raise RuntimeError('Competing maintenance did not refuse')
    allowed = subprocess.run(command, capture_output=True, text=True, timeout=12)
    if allowed.returncode != 0: raise RuntimeError('Fresh idle admission failed: '+allowed.stderr)
    with tempfile.TemporaryDirectory(prefix='hpr-maintenance-acceptance-') as directory:
        state = Path(directory); marker = state/'operational-mutation'
        # Synthetic fixture ONLY, never change the central session or live latch.
        protected = {**guard.fetch(url), 'blocked': True, 'phase': 'active'}
        with patch.object(guard, 'STATE', state), patch.object(guard, 'fetch', return_value=protected):
            code = guard.guarded_main(lambda: marker.write_text('must never execute'))
            if code != 75 or marker.exists(): raise RuntimeError('Protected fixture admitted mutation')
            evidence = (state/'protection.json').read_bytes()
        with patch.object(guard, 'STATE', state), patch.object(guard, 'fetch', side_effect=TimeoutError):
            code = guard.guarded_main(lambda: marker.write_text('must never execute'))
            if code != 75 or marker.exists() or evidence != (state/'protection.json').read_bytes():
                raise RuntimeError('Unavailable authority cleared fixture latch')
    with guard.admission():
        if fingerprint() != before or hashlib.sha256(guard.CONFIG.read_bytes()).hexdigest() != config_hash:
            raise RuntimeError('Operational processes/config changed during acceptance')
    print(json.dumps({'real_fresh_idle_admission': True, 'real_competing_lock_refused': True,
        'isolated_active_session_refused_before_mutation': True,
        'isolated_unavailable_authority_preserved_latch': True,
        'services_restarted': [], 'operational_configuration_and_processes_unchanged': True,
        'central_session_changed': False}))


if __name__ == '__main__': main()

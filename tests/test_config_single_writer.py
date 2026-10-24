import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import yaml

from hpr_gateway.config import load_config
from hpr_gateway.config_transaction import MARKER, config_lock
from hpr_gateway.services.trike_config_sync import ProfileApplier, SyncEvidence, profile_hash
from hpr_gateway.services import gpio_config_sync, tpms_config_sync, power_cadence_config_sync
import test_trike_config_sync as fixtures


class SingleWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'hpr.yaml'
        self.data = fixtures.TrikeConfigSyncTests._valid_config()
        self.path.write_text(yaml.safe_dump(self.data, sort_keys=False), encoding='utf-8')
        d = self.data
        self.profile = {'schema_version': 1, 'trike_id': 'trike1',
            'identity': {'display_name': d['hpr']['display_name'], 'hostname': d['pi']['hostname']},
            'heart_rate': {'enabled': False, 'monitors': d['heart_rate']['monitors']},
            'derailleur': {'enabled': False, 'device_name': d['derailleur']['device_name'], 'mac': d['derailleur']['mac']},
            'video': {'enabled': d['services']['video']['enabled'], 'cameras': copy.deepcopy(d['video']['cameras'])},
            'tpms': {'tpms_enabled': d['services']['tpms']['enabled'], 'sensors': d['tpms']['sensors']},
            'power_cadence': {'power_cadence_enabled': d['services']['power_cadence']['enabled'], 'devices': d['power_cadence']['devices']},
            'gpio': {'gpio_enabled': False, 'gpio': d['gpio']}}
        self.systemd = Mock()
        self.applier = ProfileApplier(str(self.path), self.systemd)

    def envelope(self):
        return {'profile': copy.deepcopy(self.profile), 'hash': profile_hash(self.profile)}

    def apply(self):
        self.applier.apply(self.envelope())

    def test_noop_checks_all_sections_and_never_restarts(self):
        self.apply()
        self.systemd.reset_mock()
        before = self.path.read_bytes()
        self.apply()
        self.assertEqual(before, self.path.read_bytes())
        self.systemd.apply.assert_not_called()
        for key, value, expected in (
            ('hpr.display_name', 'drift', None),
            ('heart_rate.monitors', {}, 'hpr-heart-rate.service'),
            ('derailleur.device_name', 'drift', 'hpr-derailleur.service'),
            ('tpms.sensors.tpms1.position', 'rear', 'hpr-tpms.service'),
            ('power_cadence.devices.assioma_left.battery_poll_seconds', 120, 'hpr-power-cadence.service'),
            ('gpio.ambient_lux.on_threshold', 12345, 'hpr-gpio-control.service'),
        ):
            with self.subTest(key=key):
                data = yaml.safe_load(self.path.read_text())
                node = data
                parts = key.split('.')
                for part in parts[:-1]: node = node.setdefault(part, {})
                original = node.get(parts[-1])
                # Ensure the alternate content actually differs.
                if original == value: value = {'drift': {}} if isinstance(value, dict) else str(value) + '-drift'
                node[parts[-1]] = value
                self.path.write_text(yaml.safe_dump(data, sort_keys=False))
                self.systemd.reset_mock()
                self.apply()
                calls = [c.args[0] for c in self.systemd.apply.call_args_list]
                self.assertEqual(calls, [] if expected is None else [expected])
                self.assertEqual(yaml.safe_load(self.path.read_text())['configuration']['trike_hash'], self.envelope()['hash'])

    def test_tpms_or_rider_edit_does_not_touch_gpio_or_video(self):
        self.apply(); self.systemd.reset_mock()
        self.profile['tpms']['tpms_enabled'] = not self.profile['tpms']['tpms_enabled']
        self.apply()
        self.assertEqual([c.args[0] for c in self.systemd.apply.call_args_list], ['hpr-tpms.service'])
        self.systemd.reset_mock()
        self.profile['heart_rate']['enabled'] = True
        self.profile['heart_rate']['monitors']['hrm1']['enabled'] = True
        self.apply()
        self.assertEqual([c.args[0] for c in self.systemd.apply.call_args_list], ['hpr-heart-rate.service'])

    def test_incomplete_or_nonboolean_profile_preserved(self):
        before = self.path.read_bytes()
        del self.profile['gpio']
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(before, self.path.read_bytes())
        self.systemd.apply.assert_not_called()

    def test_migration_rejects_all_legacy_apply_and_recover(self):
        self.path.with_name(MARKER).write_text('2')
        before = self.path.read_bytes()
        for module in (gpio_config_sync, tpms_config_sync, power_cadence_config_sync):
            applier = module.ProfileApplier(str(self.path))
            with self.assertRaisesRegex(RuntimeError, 'retired'): applier.apply({})
            with self.assertRaisesRegex(RuntimeError, 'retired'): applier.recover()
        self.assertEqual(before, self.path.read_bytes())

    def test_service_failure_rolls_back_before_next_transaction(self):
        self.apply(); before = self.path.read_bytes()
        self.profile['tpms']['tpms_enabled'] = not self.profile['tpms']['tpms_enabled']
        self.systemd.apply.side_effect = [RuntimeError('restart failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'restart failed'): self.apply()
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.applier.pending.exists())
        self.assertEqual(self.systemd.apply.call_args_list[-1].args,
                         ('hpr-tpms.service', self.data['services']['tpms']['enabled']))

    def test_foreign_pending_recovered_under_same_lock_and_multiple_fail_closed(self):
        p = self.path.with_name('.tpms-sync-pending.json'); p.write_text('{}')
        with patch.object(tpms_config_sync.ProfileApplier, 'recover') as recover:
            recover.side_effect = lambda: p.unlink()
            self.apply(); recover.assert_called_once()
        p.write_text('{}'); self.applier.pending.write_text('{}')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Multiple interrupted'): self.apply()
        self.assertEqual(before, self.path.read_bytes())

    def test_capability_fresh_heartbeat_readback_and_drift(self):
        self.apply(); self.path.with_name(MARKER).write_text('2')
        evidence = SyncEvidence(self.applier); evidence.envelope = self.envelope()
        with patch('hpr_gateway.services.trike_config_sync.subprocess.run', return_value=Mock(returncode=3)):
            a, b = evidence.status(), evidence.status()
            self.assertTrue(a['single_writer']); self.assertEqual(a['state'], 'applied')
            self.assertEqual(b['seq'], a['seq'] + 1); self.assertEqual(a['boot_id'], b['boot_id'])
            self.assertEqual(a['section_hashes']['tpms'], profile_hash(self.profile['tpms']))
            d = yaml.safe_load(self.path.read_text()); d['hpr']['display_name'] = 'unexpected'
            self.path.write_text(yaml.safe_dump(d))
            state = evidence.status(); self.assertEqual(state['state'], 'drift'); self.assertEqual(state['section_hashes'], {})
        with patch('hpr_gateway.services.trike_config_sync.subprocess.run', return_value=Mock(returncode=0)):
            self.assertFalse(evidence.status()['single_writer'])

    def test_cross_process_lock_serializes_read_modify_write(self):
        counter = self.path.with_name('counter'); counter.write_text('0')
        code = ('import time,sys;from pathlib import Path;'
                'from hpr_gateway.config_transaction import config_lock;'
                'p=Path(sys.argv[1]);c=p.with_name("counter");'
                '\nwith config_lock(p):\n n=int(c.read_text());time.sleep(.15);c.write_text(str(n+1))')
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.path)]) for _ in range(3)]
        for process in processes: self.assertEqual(process.wait(timeout=15), 0)
        self.assertEqual(counter.read_text(), '3')


if __name__ == '__main__': unittest.main()

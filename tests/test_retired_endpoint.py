import copy
import importlib.util
from pathlib import Path
import unittest
from hpr_gateway.config import HprConfig
from test_native_mqtt_only import config

spec = importlib.util.spec_from_file_location('endpoint_migration', Path(__file__).resolve().parents[1] / 'scripts/remove-retired-pi-network-endpoint.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class RetiredEndpointTests(unittest.TestCase):
    def test_only_requested_inert_block_removed_and_original_not_modified(self):
        data = copy.deepcopy(config().data)
        data['network']['retired_endpoint'] = {'host': 'retired', 'port': 8123, 'url': 'http://retired', 'username': 'x', 'password': 'x'}
        data['configuration'] = {'trike_hash': 'unchanged'}
        c = HprConfig(data, 'test.yaml')
        before = copy.deepcopy(c.data)
        result = migration.candidate(c, 'retired_endpoint')
        expected = copy.deepcopy(before); del expected['network']['retired_endpoint']
        self.assertEqual(result, expected)
        self.assertEqual(c.data, before)
        self.assertEqual(result['configuration']['trike_hash'], 'unchanged')

    def test_active_native_settings_cannot_be_removed(self):
        for key in ('mqtt', 'video', 'mediamtx', 'tailscale_required'):
            with self.subTest(key=key):
                with self.assertRaises(ValueError): migration.candidate(config(), key)

    def test_unknown_or_missing_block_rejected(self):
        with self.assertRaises(ValueError): migration.candidate(config(), 'missing')
        c = config(); c.data['network']['custom'] = {'host': 'x', 'port': 80, 'unidentified': True}
        with self.assertRaises(ValueError): migration.candidate(c, 'custom')


if __name__ == '__main__': unittest.main()

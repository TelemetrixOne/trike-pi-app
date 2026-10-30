import importlib.util
from pathlib import Path
import unittest
from test_native_mqtt_only import config


spec = importlib.util.spec_from_file_location('assessment_deploy', Path(__file__).parents[1] / 'scripts/deploy-pi-assessment-health.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AssessmentDeployTests(unittest.TestCase):
    def test_metadata_migration_preserves_all_hardware_and_credentials(self):
        c = config()
        c.data['configuration'] = {'trike_hash': 'central-hash', 'gpio_hash': 'obsolete'}
        c.data['services']['heartbeat'] = {'enabled': True}
        for name in ('gpio_config_sync', 'tpms_config_sync', 'power_cadence_config_sync'):
            c.data['services'][name] = {'enabled': True}
        before = __import__('copy').deepcopy(c.data)
        migrated = module.updated_data(c, {'interval_seconds': 60, 'services': ['hpr-gps.service'], 'limits': {}})
        expected = __import__('copy').deepcopy(before)
        expected['services']['health_agent'] = {'enabled': True}
        expected['services']['heartbeat'].update(publish_legacy_topics=False, legacy_topics=[])
        for name in ('gpio_config_sync', 'tpms_config_sync', 'power_cadence_config_sync'):
            expected['services'][name]['enabled'] = False
        expected['configuration'].pop('gpio_hash')
        expected['health_agent'] = {'interval_seconds': 60, 'units': ['hpr-gps.service'], 'limits': {}}
        self.assertEqual(migrated, expected)
        self.assertEqual(c.data, before)
        with self.assertRaises(RuntimeError):
            module.updated_data(c, {'custom_enhancement': True})

    def test_deployment_excludes_gpio_drs_and_video_runtime(self):
        import json
        manifest = json.loads((module.SOURCE / 'scripts/assessment-health-baseline.json').read_text())
        self.assertNotIn('hpr_gateway/services/gpio_control.py', manifest['install'])
        self.assertNotIn('hpr_gateway/drs_protocol.py', manifest['install'])
        self.assertNotIn('bin/hpr-video-native.py', manifest['install'])
        self.assertTrue(set(module.PROTECTED).isdisjoint(module.AFFECTED))

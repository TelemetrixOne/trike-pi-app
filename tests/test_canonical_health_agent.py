import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from hpr_gateway.services.health_agent import HealthAgent
from test_native_mqtt_only import config


class CanonicalHealthAgentTests(unittest.TestCase):
    def test_generic_native_mode_persists_but_never_restarts_services(self):
        with tempfile.TemporaryDirectory() as folder:
            c = config(); c.data['services']['services_health'] = {'units': {'gps_health': 'hpr-gps.service'}}
            client = Mock(); agent = HealthAgent(c, client, Path(folder) / 'mode.json')
            self.assertEqual(agent.root, 'custom-root/trike1/agents/health')
            agent.on_message(client, None, SimpleNamespace(topic=agent.root + '/enabled/set', payload=b'ON'))
            self.assertTrue(agent.enabled)
            resumed = HealthAgent(c, Mock(), Path(folder) / 'mode.json')
            self.assertTrue(resumed.enabled)
            with patch.object(agent, 'metrics', return_value={'cpu_temperature_c': 37, 'disk_used_pct': 5, 'throttled_raw': '0x0'}), \
                 patch.object(agent, 'service_state', return_value={'unit': 'hpr-gps.service', 'active': False, 'state': 'failed'}):
                value = agent.check()
            self.assertEqual(value['remediations'], [])
            self.assertEqual(value['status'], 'critical')
            self.assertEqual(value['remediation_owner'], 'systemd_and_dedicated_recovery')
            self.assertNotIn('restart', json.dumps(value['remediations']))


if __name__ == '__main__': unittest.main()

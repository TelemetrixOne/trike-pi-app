import copy
import unittest
from unittest.mock import Mock, call

from hpr_gateway.config import HprConfig
from hpr_gateway.credential_reload import CredentialReload
from test_native_mqtt_only import config


class CredentialReloadTests(unittest.TestCase):
    def test_credentials_only_network_lifecycle_no_controller_reinitialisation(self):
        original = config()
        client = Mock()
        worker = CredentialReload(client, original)
        data = copy.deepcopy(original.data)
        data['network']['mqtt'].update(username='new-private-user', password='new-private-secret')
        updated = HprConfig(data, original.path)
        self.assertTrue(worker.apply(updated))
        self.assertEqual(client.mock_calls, [call.disconnect(), call.loop_stop(),
            call.username_pw_set('new-private-user', 'new-private-secret'),
            call.connect_async(original.mqtt.host, original.mqtt.port, keepalive=60), call.loop_start()])
        client.reset_mock()
        self.assertFalse(worker.apply(updated))
        client.assert_not_called()
        self.assertEqual(client.mock_calls, [])

    def test_changed_broker_identity_or_gpio_refused_before_network_disconnect(self):
        original = config()
        for key in ('broker', 'identity', 'gpio'):
            data = copy.deepcopy(original.data)
            if key == 'broker': data['network']['mqtt']['host'] = 'other-host'
            if key == 'identity': data['hpr']['trike_id'] = 'trike2'
            if key == 'gpio': data['gpio'] = {'service': {'poll_interval_seconds': .1}}
            client = Mock()
            with self.assertRaises(ValueError):
                CredentialReload(client, original).apply(HprConfig(data, original.path))
            self.assertEqual(client.mock_calls, [])

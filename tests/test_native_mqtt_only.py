import queue
import unittest
from unittest.mock import Mock, patch

from hpr_gateway.config import ConfigError, HprConfig
from hpr_gateway.services import gpio_control as gpio


def config(host='mqtt.example'):
    return HprConfig({'hpr': {'trike_id': 'trike1', 'display_name': 'Test',
                              'topic_root': 'custom-root', 'environment': 'test'},
                      'pi': {'hostname': 'test-pi'}, 'services': {},
                      'network': {'mqtt': {'host': host, 'port': 1883,
                                           'username': 'test', 'password': 'test'}}}, 'test.yaml')


class NativeMqttOnlyTests(unittest.TestCase):
    def test_only_native_mqtt_endpoint_is_required(self):
        c = config()
        self.assertEqual(c.mqtt.host, 'mqtt.example')
        self.assertEqual(set(c.data['network']), {'mqtt'})

    def test_unresolved_mqtt_endpoint_is_still_rejected(self):
        with self.assertRaises(ConfigError):
            config('auto')

    def test_configuration_keeps_native_options_only(self):
        previous = (gpio.MQTT, gpio.SERVICE, gpio.TELEMETRY, gpio.ADC, gpio.INPUTS,
                    gpio.TRIKES, gpio.DRS, gpio.AMBIENT_AUTO, gpio.HORN_REMOTE_PULSE_SECONDS)
        self.addCleanup(self._restore, previous)
        gpio.configure(config())
        self.assertEqual(set(gpio.MQTT), {'host', 'port', 'username', 'password', 'client_id',
                                         'topic_root', 'gpio_evidence_topic', 'qos', 'retain_state'})
        self.assertEqual(set(gpio.SERVICE), {'republish_state_on_reconnect',
                                            'poll_interval_seconds', 'log_level'})

    @staticmethod
    def _restore(values):
        (gpio.MQTT, gpio.SERVICE, gpio.TELEMETRY, gpio.ADC, gpio.INPUTS,
         gpio.TRIKES, gpio.DRS, gpio.AMBIENT_AUTO, gpio.HORN_REMOTE_PULSE_SECONDS) = values

    def test_reconnect_republishes_native_state_without_actuating(self):
        service = object.__new__(gpio.GPIOControlService)
        service._circuits, service._sense_inputs = {}, {}
        service._drs = gpio.DRSRuntime(12, 333, 556, 1444)
        service._events = queue.Queue()
        service._telemetry = Mock()
        service._publish = Mock()
        service._events.put(('connected', None))
        with patch.object(gpio, 'MQTT', {'topic_root': 'custom-root'}), \
             patch.object(gpio, 'DRS', {'trike': 'trike1'}), \
             patch.object(gpio, 'SERVICE', {'republish_state_on_reconnect': True}), \
             patch.object(gpio, 'PWMOutputDevice') as actuator:
            service._drain_events()
            actuator.assert_not_called()
        self.assertEqual([c.args[0] for c in service._publish.call_args_list], [
            'custom-root/trike1/control/drs/state', 'custom-root/trike1/control/drs/mode',
            'custom-root/trike1/control/drs/source'])
        service._telemetry.publish_latest.assert_called_once()

    def test_telemetry_start_has_no_discovery_publication(self):
        publish = Mock()
        with patch.object(gpio, 'TELEMETRY', {'enabled': True, 'sample_interval_seconds': 1,
                                            'retry_interval_seconds': 10, 'moving_average_samples': 5}), \
             patch.object(gpio, 'ADC', {'battery_capacity_ah': 5}), \
             patch.object(gpio.threading, 'Thread') as worker:
            monitor = gpio.TelemetryMonitor(Mock(), publish)
            monitor.start()
            worker.return_value.start.assert_called_once()
        publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()

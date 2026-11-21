"""Battery swaps, failed measurements and loaded-voltage estimation regressions."""
import json
import sys
from types import SimpleNamespace, ModuleType
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from hpr_gateway.battery_soc import BatteryEstimator, REFERENCE_CURVE, validate_soc_config, voltage_soc
from hpr_gateway.battery_protocol import parse_swap_request
from hpr_gateway.services import gpio_control as gpio


class BatterySocTests(unittest.TestCase):
    def setUp(self):
        for target, values in ((gpio.MQTT, {'battery_status_topic': 'test/status', 'topic_root': 'test'}),
                               (gpio.TELEMETRY, {'trike': 'test-trike'})):
            patcher = patch.dict(target, values)
            patcher.start()
            self.addCleanup(patcher.stop)

    def estimator(self):
        return BatteryEstimator({'enabled': True})

    def settle(self, estimator, voltage, start=0):
        for second in range(7):
            estimator.update(voltage, 1, start + second)

    def test_reference_interpolation_and_no_legacy_26_percent_remap(self):
        self.assertEqual(0, voltage_soc(15, REFERENCE_CURVE))
        self.assertEqual(100, voltage_soc(21, REFERENCE_CURVE))
        self.assertAlmostEqual(50, voltage_soc(18.75, REFERENCE_CURVE))
        estimator = self.estimator()
        self.settle(estimator, 19)
        self.assertEqual(60, estimator.snapshot()['soc_pct'])
        self.assertEqual('reference_estimate', estimator.snapshot()['quality'])

    def test_reducing_load_never_increases_soc_until_an_explicit_swap(self):
        estimator = self.estimator()
        self.settle(estimator, 18)
        for second in range(7, 100):
            estimator.update(21, 0, second)
        self.assertEqual(20, estimator.soc)
        estimator.request_swap('replacement', 100)
        self.settle(estimator, 21, 100)
        self.assertEqual(100, estimator.soc)

    def test_restart_keeps_usage_and_cannot_rebound_or_display_without_fresh_voltage(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'estimate.json'
            estimator = BatteryEstimator({'enabled': True}, state_path=state)
            self.settle(estimator, 18)
            estimator.ah_used = 1.25
            estimator.checkpoint(7, force=True)
            restored = BatteryEstimator({'enabled': True}, state_path=state)
            self.assertIsNone(restored.snapshot()['soc_pct'])
            self.settle(restored, 21)
            self.assertEqual(20, restored.soc)
            self.assertGreaterEqual(restored.ah_used, 1.25)
            restored.poll(30)
            self.assertIsNone(restored.snapshot()['soc_pct'])

    def test_unfinished_or_rejected_swap_is_not_completed_by_a_restart(self):
        for timeout in [False, True]:
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)/'estimate.json'
                estimator = BatteryEstimator({'enabled': True}, state_path=state)
                self.settle(estimator, 18)
                estimator.request_swap('replacement', 10)
                if timeout:
                    estimator.poll(41)
                restored = BatteryEstimator({'enabled': True}, state_path=state)
                self.settle(restored, 21)
                self.assertEqual('rejected', restored.state)
                self.assertIsNone(restored.snapshot()['soc_pct'])

    def test_invalid_or_different_curve_checkpoint_cannot_manufacture_a_reading(self):
        for value in ['broken', '{"version":1}', 'null']:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)/'estimate.json';state.write_text(value)
                restored = BatteryEstimator({'enabled': True}, state_path=state)
                self.assertIsNone(restored.snapshot()['soc_pct'])
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'estimate.json'
            estimator = BatteryEstimator({'enabled': True}, state_path=state)
            self.settle(estimator, 18)
            changed = BatteryEstimator({'enabled': True, 'voltage_soc_curve': [[15, 0], [18, 50], [21, 100]]}, state_path=state)
            self.assertIsNone(changed.snapshot()['soc_pct'])
            self.settle(changed, 18)
            self.assertEqual(50, changed.soc)

    def test_calibrated_curve_matches_recorded_points_and_interpolates(self):
        profile = json.loads((Path(__file__).parents[1]/'config/calibrations/trike1-battery-20261121.json').read_text())
        curve = profile['voltage_soc_curve']
        for voltage, percentage in curve:
            self.assertAlmostEqual(percentage, voltage_soc(voltage, curve))
        self.assertAlmostEqual(62.5, voltage_soc((17.31+18.24)/2, curve))
        self.assertTrue(85 < voltage_soc(18.6, curve) < 90)
        estimator = BatteryEstimator(profile)
        self.settle(estimator, 20.183)
        self.assertEqual(100, estimator.snapshot()['soc_pct'])
        self.assertEqual('configured_curve_estimate', estimator.snapshot()['quality'])

    def test_unwritable_checkpoint_does_not_break_sampling_or_retry_every_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'estimate.json'
            estimator = BatteryEstimator({'enabled': True}, state_path=state)
            with patch('hpr_gateway.battery_soc.tempfile.mkstemp', side_effect=PermissionError) as write:
                self.settle(estimator, 19)
                for second in range(7, 20):
                    estimator.update(19, 1, second)
                self.assertEqual(1, write.call_count)
                self.assertEqual(60, estimator.snapshot()['soc_pct'])
            estimator.update(19, 1, 36)
            self.settle(estimator, 19, 37)
            self.assertTrue(state.exists())

    def test_explicit_swap_discards_previous_consumption_only_after_stable_voltage(self):
        estimator = self.estimator()
        self.settle(estimator, 18)
        estimator.ah_used = 4
        self.assertTrue(estimator.request_swap('first', 10))
        self.assertFalse(estimator.request_swap('second', 11))
        estimator.update(20.5, 1, 10)
        self.assertIsNone(estimator.snapshot()['soc_pct'])
        self.assertEqual(4, estimator.ah_used)
        self.settle(estimator, 20.5, 11)
        self.assertEqual('completed', estimator.state)
        self.assertEqual(95, estimator.snapshot()['soc_pct'])
        self.assertLess(estimator.ah_used, 0.001)

    def test_request_duplicate_does_not_clear_settling_samples(self):
        estimator = self.estimator()
        estimator.request_swap('same', 0)
        estimator.update(20, 0, 1)
        # The protocol layer recognises duplicate IDs before calling request_swap.
        service = self.monitor(estimator)
        payload = self.request('same')
        service._battery_requests.put((payload, False))
        with patch('hpr_gateway.services.gpio_control.parse_swap_request', return_value={'request_id': 'same'}), \
             patch.object(gpio.time, 'monotonic', return_value=2):
            service._process_battery_requests()
        self.assertEqual(1, len(estimator.samples))
        self.assertEqual(30, estimator.pending_until)

    def test_invalid_negative_nonfinite_voltage_never_reports_zero_or_initialises(self):
        for invalid in (-1.2, 0, 8, float('nan'), float('inf'), 24):
            with self.subTest(voltage=invalid):
                estimator = self.estimator()
                estimator.update(invalid, 0, 0)
                snapshot = estimator.snapshot()
                self.assertIsNone(snapshot['soc_pct'])
                self.assertIsNone(snapshot['voltage_v'])
                self.assertEqual('invalid', snapshot['state'])
                self.settle(estimator, 20.5, 1)
                self.assertEqual(95, estimator.snapshot()['soc_pct'])

    def test_adc_recovery_does_not_reset_previous_pack_usage(self):
        estimator = self.estimator()
        self.settle(estimator, 19)
        estimator.ah_used = 2.5
        estimator.invalidate('adc_failed')
        estimator.update(19, 0, 10)
        self.assertIsNone(estimator.snapshot()['soc_pct'])
        self.settle(estimator, 19, 11)
        self.assertGreaterEqual(estimator.ah_used, 2.5)
        self.assertLess(estimator.ah_used, 2.51)
        self.assertEqual(60, estimator.snapshot()['soc_pct'])

    def test_load_step_does_not_reinitialise_or_jump_to_full(self):
        estimator = self.estimator()
        self.settle(estimator, 18)
        estimator.ah_used = 2
        estimator.update(21, 0, 7)
        self.assertLessEqual(estimator.soc, 20.5)
        self.assertEqual(2, estimator.ah_used)
        self.assertIsNone(estimator.request_id)

    def test_stale_and_timeout_are_explicit_and_cannot_be_late_success(self):
        estimator = self.estimator()
        self.settle(estimator, 19)
        estimator.poll(20)
        self.assertEqual('stale', estimator.snapshot()['state'])
        self.assertIsNone(estimator.snapshot()['soc_pct'])
        estimator.request_swap('timeout', 25)
        estimator.poll(56)
        estimator.update(20, 0, 57)
        self.assertEqual('rejected', estimator.state)
        self.assertIsNone(estimator.snapshot()['soc_pct'])

    def test_unstable_swap_timeout_recovers_from_fresh_stable_voltage(self):
        curve = [[12.6, 0], [18.74, 90], [20.18, 100]]
        estimator = BatteryEstimator({'enabled': True, 'voltage_soc_curve': curve})
        self.settle(estimator, 18)
        estimator.ah_used = 2
        estimator.request_swap('unstable', 10)
        for second in range(10, 40):
            estimator.update(18 if second % 2 else 20, 0, second)
        estimator.poll(40)
        estimator.update(23, 0, 41)
        estimator.update(19.8, 0, 42)
        self.assertEqual('rejected', estimator.state)
        self.assertIsNone(estimator.snapshot()['soc_pct'])
        self.settle(estimator, 19.8, 51)
        self.assertEqual('ready', estimator.state)
        self.assertIsNone(estimator.request_id)
        self.assertLess(estimator.ah_used, 0.001)
        self.assertAlmostEqual(97.4, estimator.snapshot()['soc_pct'], places=1)

    def test_curve_and_timing_configuration_rejects_mistakes(self):
        for config in ({'enabled': 1}, {'profile': 'unknown'}, {'filter_seconds': 0},
                       {'stable_spread_v': float('nan')}, {'unexpected': 2},
                       {'voltage_soc_curve': [[15, 0], [15, 50], [21, 100]]},
                       {'voltage_soc_curve': [[15, 0], [18, 60], [21, 50]]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_soc_config(config)

    def test_long_window_at_fast_supported_sample_rate_can_settle(self):
        estimator = BatteryEstimator({'enabled': True, 'stable_seconds': 30}, sample_seconds=0.1)
        for index in range(310):
            estimator.update(19, 0, index/10)
        self.assertEqual(60, estimator.snapshot()['soc_pct'])

    def test_sampling_rate_must_fill_stable_window(self):
        from hpr_gateway.gpio_profile import validate_gpio
        with self.assertRaises(ValueError):
            validate_gpio({'adc': {'battery_soc': {'enabled': True}},
                           'telemetry': {'sample_interval_seconds': 30}})

    def request(self, identity=None, **updates):
        return json.dumps({'schema_version': 1, 'request_id': identity or str(uuid.uuid4()),
                           'action': 'BATTERY_SWAPPED', 'issued_at': 100, 'expires_at': 105,
                           **updates}).encode()

    def test_expired_retained_unsynchronised_and_duplicate_fields_refuse(self):
        for raw, retained, now, clock in ((self.request(), True, 101, True),
                (self.request(), False, 106, True), (self.request(), False, 101, False),
                (self.request(expires_at=200), False, 101, True),
                (self.request(action='START'), False, 101, True),
                (b'{"request_id":"a","request_id":"b"}', False, 101, True)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_swap_request(raw, retained, now, clock)
        self.assertEqual('BATTERY_SWAPPED', parse_swap_request(self.request(), False, 101, True)['action'])

    def monitor(self, estimator=None):
        with patch.object(gpio, 'TELEMETRY', {'enabled': True, 'sample_interval_seconds': 1,
                    'retry_interval_seconds': 10, 'moving_average_samples': 5}), \
             patch.object(gpio, 'ADC', {'battery_capacity_ah': 5, 'battery_soc': {'enabled': True}}):
            monitor = gpio.TelemetryMonitor(Mock(), Mock())
        if estimator is not None:
            monitor._battery = estimator
        monitor._battery_clock = Mock(ready=True)
        return monitor

    def test_persistent_replay_journal_refuses_reset_after_monitor_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            from hpr_gateway.demo_protocol import DemoJournal
            identity = str(uuid.uuid4())
            for index in range(2):
                monitor = self.monitor()
                monitor._battery_journal = DemoJournal(Path(directory)/'requests.db')
                monitor._battery_requests.put((self.request(identity), False))
                with patch.object(gpio.time, 'time', return_value=101):
                    monitor._process_battery_requests()
                if index == 0:
                    self.assertEqual('settling', monitor._battery.state)
                    self.assertEqual(identity, monitor._battery.request_id)
                else:
                    report = json.loads(monitor._publish.call_args.args[1])
                    self.assertEqual('rejected', report['state'])
                    self.assertEqual('request_already_processed', report['reason'])

    def test_status_is_live_and_bounded_while_invalid_measurement_keeps_lights_sampling(self):
        monitor = self.monitor()
        monitor._hardware_ready = True
        with patch.dict(gpio.MQTT, {'battery_status_topic': 'test/status'}):
            monitor._sample_battery_curve(-1.2, 0, 2.4, 50, 0)
            monitor._publish_battery_status()
            monitor._publish_battery_status()
        reports = [call for call in monitor._publish.call_args_list if call.args[0] == 'test/status']
        self.assertEqual(1, len(reports))
        self.assertEqual({'retain': False, 'qos': 1}, reports[0].kwargs)
        report = json.loads(reports[0].args[1])
        self.assertIsNone(report['soc_pct'])
        self.assertFalse(any(call.args[0].endswith('/soc') for call in monitor._publish.call_args_list))
        self.assertEqual(2.4, monitor.latest_light_voltage())

    def test_real_adc_initialisation_does_not_seed_from_bad_voltage_or_reset_usage(self):
        monitor = self.monitor()
        monitor._battery.ah_used = 2.75
        monitor._battery.initialised = True
        monitor._battery.soc = 60
        package = ModuleType('adafruit_ads1x15'); package.__path__ = []
        ads = ModuleType('adafruit_ads1x15.ads1115')
        ads.ADS1115 = type('Driver', (), {'__init__': lambda self, *a, **k: None})
        analog = ModuleType('adafruit_ads1x15.analog_in')
        analog.AnalogIn = lambda device, channel, *a: SimpleNamespace(voltage=-0.15 if channel == 2 else 2.4)
        config = {'i2c_address': '0x48', 'ads_gain': 1, 'shunt_p': 'P0', 'shunt_n': 'P1',
                  'battery_channel': 'P2', 'light_channel': 'P3', 'battery_divider_r1_ohm': 68000,
                  'battery_divider_r2_ohm': 9840, 'light_dark_voltage': 1.5, 'light_bright_voltage': 3.13}
        modules = {'board': SimpleNamespace(SCL='SCL', SDA='SDA'),
                   'busio': SimpleNamespace(I2C=Mock()), 'adafruit_ads1x15': package,
                   'adafruit_ads1x15.ads1115': ads, 'adafruit_ads1x15.analog_in': analog}
        with patch.dict(sys.modules, modules), patch.dict(gpio.ADC, config):
            monitor._try_initialise(force=True)
        self.assertTrue(monitor._hardware_ready)
        self.assertEqual(2.75, monitor._battery.ah_used)
        self.assertEqual(60, monitor._battery.soc)
        self.assertEqual([], list(monitor._battery_voltage_samples))

    def test_main_gpio_owner_only_queues_battery_request(self):
        service = object.__new__(gpio.GPIOControlService)
        service._telemetry = Mock()
        message = SimpleNamespace(topic='test/battery/command', payload=self.request(), retain=False)
        with patch.dict(gpio.MQTT, {'battery_command_topic': message.topic}):
            service._handle_message(message)
        service._telemetry.queue_battery_swap.assert_called_once_with(message)


if __name__ == '__main__': unittest.main()

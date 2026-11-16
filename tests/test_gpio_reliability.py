import queue
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from hpr_gateway import gpio_profile
from hpr_gateway.services import gpio_control as gpio


class GpioReliabilityTests(unittest.TestCase):
    def test_noisy_switch_never_requests_interrupts_and_requires_stable_samples(self):
        previous = Device.pin_factory
        Device.pin_factory = MockFactory()
        try:
            with patch.object(gpio.time, "monotonic", return_value=100):
                button = gpio.PolledInput(6, pull_up=True)
            with button:
                self.assertIsNone(button.pin.when_changed)
                for instant in range(10):
                    with patch.object(gpio.time, "monotonic", return_value=100 + instant * 0.01):
                        button.pin.drive_low() if instant % 2 == 0 else button.pin.drive_high()
                        self.assertFalse(button.is_pressed)
                with patch.object(gpio.time, "monotonic", return_value=101):
                    button.pin.drive_low()
                    self.assertFalse(button.is_pressed)
                with patch.object(gpio.time, "monotonic", return_value=101.06):
                    self.assertTrue(button.is_pressed)
                self.assertIsNone(button.pin.when_changed)
        finally:
            Device.pin_factory.close()
            Device.pin_factory = previous

    def test_mqtt_callback_enqueues_without_actuating_and_main_owner_handles_it(self):
        service = object.__new__(gpio.GPIOControlService)
        service._events = queue.Queue(maxsize=64)
        service._handle_message = Mock()
        message = SimpleNamespace(topic="test/control/headlight/set", payload=b"ON", retain=False)
        service._on_message(None, None, message)
        service._handle_message.assert_not_called()
        service._drain_events()
        service._handle_message.assert_called_once_with(message)

    def test_retained_horn_and_drs_commands_do_not_override_boot_state(self):
        service = object.__new__(gpio.GPIOControlService)
        service._set_drs_mode = Mock()
        service._apply_logic = Mock()
        for circuit in ("horn", "drs", "aux_lights", "headlight"):
            service._handle_message(SimpleNamespace(topic=f"test/control/{circuit}/set",
                                                    payload=b"ON", retain=True))
        service._set_drs_mode.assert_not_called()
        service._apply_logic.assert_not_called()

    def test_drs_transition_is_nonblocking_and_duplicate_does_not_extend_release(self):
        service = object.__new__(gpio.GPIOControlService)
        service._drs_lock = threading.RLock()
        output = Mock(value=0.333)
        service._drs = gpio.DRSRuntime(12, 333, 1000, 1500, output=output,
                                      current_pulse_us=1000)
        settings = {"transition_seconds": 0.5, "switch_behavior": "pulse_release",
                    "pulse_release_seconds": 0.5}
        with patch.object(gpio, "DRS", settings), patch.object(gpio.time, "sleep") as sleeper:
            with patch.object(gpio.time, "monotonic", return_value=100):
                service._set_drs("OPEN", publish=False)
            self.assertEqual(service._drs.current_pulse_us, 1000)
            with patch.object(gpio.time, "monotonic", return_value=100.25):
                service._release_drs_if_due()
            self.assertAlmostEqual(output.value, 1250 * 333 / 1000000)
            with patch.object(gpio.time, "monotonic", return_value=100.5):
                service._release_drs_if_due()
            self.assertEqual(service._drs.pulse_release_at_monotonic, 101)
            with patch.object(gpio.time, "monotonic", return_value=100.7):
                service._set_drs("OPEN", publish=False)
            self.assertEqual(service._drs.pulse_release_at_monotonic, 101)
            with patch.object(gpio.time, "monotonic", return_value=101):
                service._release_drs_if_due()
            output.close.assert_called_once()
            self.assertIsNone(service._drs.output)
            with patch.object(gpio, "PWMOutputDevice") as pwm:
                service._set_drs("OPEN", publish=False)
                pwm.assert_not_called()
            sleeper.assert_not_called()

    def test_adc_conversion_timeout_yields_instead_of_spinning(self):
        class Driver:
            def _read(self, _pin):
                while not self._conversion_complete():
                    pass
            def _conversion_complete(self):
                return False
        class Bounded(gpio.BoundedConversionMixin, Driver):
            pass
        with patch.object(gpio.time, "monotonic", side_effect=[100, 100.01, 100.05, 100.11]), \
             patch.object(gpio.time, "sleep") as sleeper:
            with self.assertRaises(TimeoutError):
                Bounded()._read(0)
        self.assertEqual(sleeper.call_count, 2)
        sleeper.assert_called_with(0.002)

    def test_adc_initialisation_wait_does_not_block_control_start(self):
        config = {"enabled": True, "sample_interval_seconds": 1, "retry_interval_seconds": 10,
                  "moving_average_samples": 5, "trike": "test"}
        entered, release = threading.Event(), threading.Event()
        with patch.object(gpio, "TELEMETRY", config), \
             patch.object(gpio, "ADC", {"battery_capacity_ah": 5}):
            monitor = gpio.TelemetryMonitor(Mock(), Mock())
            def initialise(force=False):
                entered.set()
                release.wait(1)
            monitor._try_initialise = initialise
            try:
                monitor.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(release.is_set())
                self.assertTrue(monitor._worker.is_alive())
            finally:
                release.set()
                monitor.stop()
            self.assertFalse(monitor._worker.is_alive())

    def test_disconnected_gpio_does_not_queue_state_history(self):
        import paho.mqtt.client as mqtt
        service = object.__new__(gpio.GPIOControlService)
        service._mqtt_connected = False
        service._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        service._client.max_queued_messages_set(128)
        for _ in range(1000):
            service._publish("test/state", "ON")
        self.assertEqual(len(service._client._out_messages), 0)
        self.assertEqual(service._client.max_queued_messages, 128)

    def test_stale_or_failed_light_sensor_does_not_supply_auto_reading(self):
        monitor = object.__new__(gpio.TelemetryMonitor)
        monitor._sample_interval_sec = 1
        monitor._hardware_ready = True
        monitor._last_sample_monotonic = 100
        monitor._light_voltage_samples = deque([1.5])
        monitor._light_samples = deque([50])
        with patch.object(gpio.time, "monotonic", return_value=101):
            self.assertEqual(monitor.latest_light_voltage(), 1.5)
            monitor._hardware_ready = False
            self.assertIsNone(monitor.latest_light_voltage())
            monitor._hardware_ready = True
        with patch.object(gpio.time, "monotonic", return_value=104):
            self.assertIsNone(monitor.latest_light_percent())

    def test_invalid_timing_is_rejected_before_service_restart(self):
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gpio_profile.validate_gpio({"service": {"poll_interval_seconds": value}})

    def test_main_control_timer_expires_horn_without_reading_adc(self):
        service = object.__new__(gpio.GPIOControlService)
        service._stop = False
        service._client = Mock()
        service._telemetry = Mock()
        service._telemetry.tick.side_effect = AssertionError("ADC cannot run on control owner")
        service._sense_inputs = {}
        service._drs = None
        service._drs_lock = threading.RLock()
        config = gpio.CircuitConfig("test", "horn", "", "", 17, "OFF", False)
        runtime = gpio.CircuitRuntime(config, Mock(), None, None, None, "ON", "ON", "REMOTE",
                                      remote_pulse_until_monotonic=100)
        service._circuits = {"test:horn": runtime}
        service._publish_runtime = Mock()
        service._publish_all_states = Mock()
        service._publish_gpio_evidence = Mock()
        service._poll_inputs = Mock()
        service._drain_events = lambda: setattr(service, "_stop", True)
        with patch.object(gpio.time, "monotonic", return_value=101), \
             patch.object(gpio.time, "sleep"), \
             patch.object(gpio, "MQTT", {"host": "offline", "port": 1883}), \
             patch.object(gpio, "SERVICE", {"poll_interval_seconds": 0.02}), \
             patch.object(gpio, "AMBIENT_AUTO", {}):
            service.start()
        runtime.output.off.assert_called_once()
        self.assertEqual(runtime.requested_state, "OFF")
        service._telemetry.tick.assert_not_called()


if __name__ == "__main__":
    unittest.main()

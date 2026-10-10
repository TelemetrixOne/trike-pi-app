import unittest
from unittest.mock import Mock

from gpiozero import Device
from gpiozero import OutputDevice
from gpiozero.pins.mock import MockFactory

from hpr_gateway.services import gpio_control
from hpr_gateway.services.gpio_control import CircuitConfig, CircuitRuntime, GPIOControlService, DRSRuntime, SenseInputConfig, SenseInputRuntime, TelemetryMonitor


class GpioInputTests(unittest.TestCase):
    def setUp(self):
        self.previous_factory = Device.pin_factory
        Device.pin_factory = MockFactory()
        self.service = object.__new__(GPIOControlService)

    def tearDown(self):
        Device.pin_factory.close()
        Device.pin_factory = self.previous_factory

    def test_drs_switch_to_ground_is_active_with_internal_pullup(self):
        config = SenseInputConfig("trike1", "drs_switch", "DRS switch", "", 6, True, False, "electrical")
        with self.service._make_button(6, True, False) as button:
            runtime = SenseInputRuntime(config, button)
            self.assertEqual(button.pin.pull, "up")
            self.assertEqual(self.service._sense_input_state(runtime), "OFF")
            button.pin.drive_low()
            self.assertEqual(self.service._sense_input_state(runtime), "ON")
            button.pin.drive_high()
            self.assertEqual(self.service._sense_input_state(runtime), "OFF")

    def test_gpio_evidence_reads_pins_without_actuating_them(self):
        output = OutputDevice(17, initial_value=False)
        self.service._circuits = {"trike1:headlight": Mock(output=output, effective_state="OFF",
            switch=None, switch_on=None, switch_auto=None)}
        self.service._sense_inputs = {}
        self.service._drs = None
        self.service._drs_lock = __import__("threading").RLock()
        try:
            evidence = self.service._gpio_evidence()
            self.assertEqual(evidence["device"], "online")
            self.assertEqual(output.value, 0)
            self.service._circuits["trike1:headlight"].effective_state = "ON"
            self.assertIn("trike1:headlight:output_readback_mismatch", self.service._gpio_evidence()["errors"])
            self.assertEqual(output.value, 0)
        finally:
            output.close()

    def test_feedback_divider_is_floating_and_active_high(self):
        config = SenseInputConfig("trike1", "headlamp", "Headlamp feedback", "", 27, None, True)
        with self.service._make_button(27, None, True) as button:
            runtime = SenseInputRuntime(config, button)
            self.assertEqual(button.pin.pull, "floating")
            button.pin.drive_low()
            self.assertEqual(self.service._sense_input_state(runtime), "OFF")
            button.pin.drive_high()
            self.assertEqual(self.service._sense_input_state(runtime), "ON")

    def test_headlight_auto_poll_only_publishes_effective_changes(self):
        config = CircuitConfig("trike1", "headlight", "Headlight", "", 5, "AUTO", True,
                               control_type="headlight_mode")
        runtime = CircuitRuntime(config, Mock(), None, None, None, "OFF", "ON", "AUTO", mode="AUTO")
        self.service._ambient_auto_enabled = Mock(return_value=True)
        self.service._ambient_auto_state = Mock(side_effect=("ON", "OFF"))
        self.service._publish_runtime = Mock()
        self.service._apply_logic(runtime, publish=False)
        self.service._publish_runtime.assert_not_called()
        self.service._apply_logic(runtime, publish=False)
        self.service._publish_runtime.assert_called_once_with(runtime)
        self.assertEqual(runtime.effective_state, "OFF")

    def test_headlight_live_command_acknowledged_but_retained_command_ignored(self):
        config = CircuitConfig("trike1", "headlight", "Headlight", "", 5, "AUTO", True,
                               control_type="headlight_mode")
        runtime = CircuitRuntime(config, Mock(), None, None, None, "OFF", "OFF", "REMOTE", mode="OFF")
        self.service._circuits = {"trike1:headlight": runtime}
        self.service._drs = None
        self.service._apply_logic = Mock()
        original_mqtt = gpio_control.MQTT
        gpio_control.MQTT = {"topic_root": "hpr"}
        try:
            self.service._on_message(None, None, Mock(topic="hpr/trike1/control/headlight/set",
                                                       payload=b"AUTO", retain=True))
            self.assertEqual(runtime.mode, "OFF")
            self.service._apply_logic.assert_not_called()
            self.service._on_message(None, None, Mock(topic="hpr/trike1/control/headlight/set",
                                                       payload=b"ON", retain=False))
            self.assertEqual(runtime.mode, "ON")
            self.service._apply_logic.assert_called_once_with(runtime, publish=True)
        finally:
            gpio_control.MQTT = original_mqtt

    def test_rider_control_maps_the_local_switch_to_servo_position(self):
        self.service._drs = DRSRuntime(12, 333, 556, 1444)
        self.service._drs_lock = __import__("threading").RLock()
        self.service._sense_inputs = {
            "trike1:drs_switch": Mock(),
        }
        self.service._sense_input_state = Mock(return_value="ON")
        self.service._set_drs = Mock()
        original_drs = gpio_control.DRS
        gpio_control.DRS = {"trike": "trike1", "rider_control_input": "drs_switch"}
        try:
            self.service._set_drs_mode("RIDER_CONTROL", publish=False)
        finally:
            gpio_control.DRS = original_drs
        self.assertEqual(self.service._drs.mode, "RIDER_CONTROL")
        self.assertEqual(self.service._drs.source, "LOCAL")
        self.service._set_drs.assert_called_once_with("OPEN", publish=False)

    def test_drs_command_accepts_named_modes_and_legacy_close(self):
        self.service._drs = DRSRuntime(12, 333, 556, 1444)
        self.service._set_drs_mode = Mock()
        original_drs = gpio_control.DRS
        gpio_control.DRS = {"trike": "trike1"}
        try:
            for payload, expected in ((b"OPEN", "OPEN"), (b"CLOSED", "CLOSED"),
                                      (b"RIDER_CONTROL", "RIDER_CONTROL"), (b"CLOSE", "CLOSED")):
                self.service._on_message(None, None, Mock(topic="hpr/trike1/control/drs/set", payload=payload))
                self.service._set_drs_mode.assert_called_with(expected)
        finally:
            gpio_control.DRS = original_drs

    def test_battery_soc_calibration_maps_observed_flat_point_to_zero(self):
        monitor = object.__new__(TelemetryMonitor)
        original_adc = gpio_control.ADC
        gpio_control.ADC = {"battery_empty_soc_pct": 26.0, "battery_empty_voltage": 8.0}
        try:
            self.assertEqual(monitor.calibrated_soc(26.0, 8.0), 0.0)
            self.assertEqual(monitor.calibrated_soc(26.0, 12.0), 0.0)
            self.assertAlmostEqual(monitor.calibrated_soc(63.0, 12.0), 50.0)
            self.assertEqual(monitor.calibrated_soc(100.0, 20.6), 100.0)
        finally:
            gpio_control.ADC = original_adc


if __name__ == "__main__":
    unittest.main()

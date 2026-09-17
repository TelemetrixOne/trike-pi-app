import unittest
from unittest.mock import Mock

from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from hpr_gateway.services import gpio_control
from hpr_gateway.services.gpio_control import GPIOControlService, DRSRuntime, SenseInputConfig, SenseInputRuntime


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

    def test_feedback_divider_is_floating_and_active_high(self):
        config = SenseInputConfig("trike1", "headlamp", "Headlamp feedback", "", 27, None, True)
        with self.service._make_button(27, None, True) as button:
            runtime = SenseInputRuntime(config, button)
            self.assertEqual(button.pin.pull, "floating")
            button.pin.drive_low()
            self.assertEqual(self.service._sense_input_state(runtime), "OFF")
            button.pin.drive_high()
            self.assertEqual(self.service._sense_input_state(runtime), "ON")

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


if __name__ == "__main__":
    unittest.main()

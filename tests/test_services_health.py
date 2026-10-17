import unittest
from datetime import datetime, timezone, timedelta

from hpr_gateway.services.services_health import gpio_health_detail, gps_health_detail, pedal_evidence, pedal_health_detail


class PedalHealthTests(unittest.TestCase):
    def test_missing_connect_message_is_recovered_by_live_power_including_zero(self):
        for power in (0, "0", 180, "180", -1):
            reading = pedal_evidence(None, None, power, 0.5)
            self.assertEqual(reading["state"], "connected")
            detail = pedal_health_detail("Good", True, [reading], "test")
            self.assertEqual(detail["device"], "connected")
            self.assertEqual(detail["overall"], "Good")

    def test_new_disconnect_wins_until_new_data_arrives(self):
        reading = pedal_evidence({"state": "disconnected"}, 0.1, 150, 1)
        self.assertEqual(reading["state"], "disconnected")
        self.assertFalse(reading["data_fresh"])
        self.assertEqual(pedal_health_detail("Good", True, [reading], "t")["overall"], "Failed")
        reading = pedal_evidence({"state": "disconnected"}, 2, 150, 0.1)
        self.assertEqual(reading["state"], "connected")

    def test_battery_warning_cannot_hide_live_data(self):
        reading = pedal_evidence({"state": "battery_read_failed"}, 0.1, 150, 1)
        self.assertEqual(reading["state"], "connected")

    def test_stale_or_invalid_samples_do_not_prove_connection(self):
        for power, age in ((150, 21), (None, 1), ("nan", 1), ("inf", 1), (True, 1), (150, None)):
            reading = pedal_evidence(None, None, power, age)
            self.assertEqual(reading["state"], "unknown")
            self.assertFalse(reading["data_fresh"])

    def test_empty_stopped_and_multiple_device_health(self):
        self.assertNotEqual(pedal_health_detail("Good", True, [], "t")["overall"], "Good")
        good = pedal_evidence(None, None, 0, 1)
        unknown = pedal_evidence(None, None, None, None)
        self.assertNotEqual(pedal_health_detail("Good", True, [good, unknown], "t")["overall"], "Good")
        self.assertEqual(pedal_health_detail("Stopped", True, [good], "t")["overall"], "Stopped")
        self.assertEqual(pedal_health_detail("Good", False, [unknown], "t")["overall"], "Disabled")


class GpsHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.source = {"state": "online", "fix_valid": True, "fix_age_seconds": 0.1,
                       "fix_mode": 3, "observed_at": self.now.isoformat()}

    def test_only_fresh_fix_is_good(self):
        self.assertEqual(gps_health_detail("Good", self.source, 1, True, "t", self.now)["overall"], "Good")
        for source, age in (({**self.source, "fix_valid": False}, 1),
                            ({**self.source, "fix_age_seconds": 20}, 1),
                            ({**self.source, "observed_at": (self.now - timedelta(seconds=60)).isoformat()}, 0),
                            ({"state": "online"}, 0), (self.source, 16), (None, None)):
            with self.subTest(source=source, age=age):
                self.assertNotEqual(gps_health_detail("Good", source, age, True, "t", self.now)["overall"], "Good")

    def test_no_fix_is_degraded_and_stopped_process_stays_stopped(self):
        nofix = {**self.source, "fix_valid": False}
        self.assertEqual(gps_health_detail("Good", nofix, 1, True, "t", self.now)["overall"], "Degraded")
        self.assertEqual(gps_health_detail("Stopped", self.source, 1, True, "t", self.now)["overall"], "Stopped")
        self.assertEqual(gps_health_detail("Good", nofix, 1, False, "t", self.now)["overall"], "Disabled")

    def test_failed_recovery_is_failed_but_never_overrides_a_new_fix(self):
        recovery = {"state": "failed", "observed_at": self.now.isoformat()}
        for valid, expected in [(False, "Failed"), (True, "Good")]:
            detail = gps_health_detail("Good", {**self.source, "fix_valid": valid}, 1, True, "t", self.now,
                                       recovery, 1)
            self.assertEqual(detail["overall"], expected)
        recovery["observed_at"] = (self.now - timedelta(seconds=60)).isoformat()
        detail = gps_health_detail("Good", {**self.source, "fix_valid": False}, 1, True, "t", self.now, recovery, 0)
        self.assertEqual(detail["overall"], "Degraded")


class GpioHealthTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"schema": "hpr.gpio_evidence.v1", "outputs_checked": 5,
                         "inputs_checked": 3, "errors": [], "device": "online"}

    def test_fresh_pin_readbacks_are_healthy(self):
        detail = gpio_health_detail("Good", self.evidence, 2.0, 15.0, "trike1")
        self.assertEqual(detail["overall"], "Good")
        self.assertEqual(detail["device"], "online")
        self.assertEqual(detail["scope"], "pi_gpio_pin_readback_not_external_load_feedback")

    def test_missing_stale_and_failed_evidence_are_not_healthy(self):
        for evidence, age in ((None, None), (self.evidence, 16.0),
                              ({**self.evidence, "errors": ["horn:output_readback_mismatch"]}, 1.0),
                              ({**self.evidence, "outputs_checked": 0}, 1.0)):
            with self.subTest(evidence=evidence, age=age):
                detail = gpio_health_detail("Good", evidence, age, 15.0, "trike1")
                self.assertEqual(detail["overall"], "Failed")

    def test_stopped_process_never_inherits_healthy_pin_evidence(self):
        detail = gpio_health_detail("Stopped", self.evidence, 1.0, 15.0, "trike1")
        self.assertEqual(detail["overall"], "Stopped")

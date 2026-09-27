import unittest

from hpr_gateway.services.services_health import gpio_health_detail


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

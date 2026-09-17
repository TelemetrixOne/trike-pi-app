import unittest
from unittest.mock import Mock, call

from hpr_gateway.services.services_health import (
    derive_device_health,
    legacy_overall,
    selected_tpms_names,
    sensor_overall,
    subscribe_source_topics,
)


class ServicesHealthTests(unittest.TestCase):
    def test_gps_requires_process_and_receiver_data(self):
        self.assertEqual(derive_device_health("Good", "online"), ("Good", "fresh", "receiver_data"))
        self.assertEqual(derive_device_health("Good", "stale"), ("Failed", "stale", "no_receiver_data"))
        self.assertEqual(
            derive_device_health("Good", "connecting"),
            ("Degraded", "waiting", "waiting_for_receiver_data"),
        )
        self.assertEqual(
            derive_device_health("Stopped", "online"),
            ("Stopped", "unknown", "service_not_running"),
        )

    def test_race_sensor_overall_respects_expectation(self):
        self.assertEqual(sensor_overall("Good", False, False), "Disabled")
        self.assertEqual(sensor_overall("Good", True, True), "Good")
        self.assertEqual(sensor_overall("Good", True, False), "Failed")
        self.assertEqual(sensor_overall("Good", True, False, waiting=True), "Degraded")
        self.assertEqual(sensor_overall("Stopped", False, False), "Stopped")
        self.assertEqual(legacy_overall("Disabled"), "Good")
        self.assertEqual(legacy_overall("Failed"), "Failed")

    def test_tpms_health_uses_ha_wheel_assignment(self):
        available = {f"tpms{number}" for number in range(1, 7)}
        assignment = {"front_left": "tpms6", "front_right": "tpms4", "rear": "tpms5"}
        self.assertEqual(selected_tpms_names(assignment, available), (["tpms6", "tpms4", "tpms5"], None))
        self.assertEqual(selected_tpms_names(None, available), ([], "assignment_unavailable"))
        self.assertEqual(
            selected_tpms_names({"front_left": "tpms1", "front_right": "tpms1", "rear": "tpms2"}, available),
            ([], "assignment_invalid_or_duplicated"),
        )

    def test_health_sources_are_resubscribed_after_mqtt_reconnect(self):
        client = Mock()
        subscribe_source_topics(client, ["hpr/trike1/nav/status", "hpr/trike1/tpms/tpms1/availability"])
        self.assertEqual(
            client.subscribe.call_args_list,
            [
                call("hpr/trike1/nav/status", qos=1),
                call("hpr/trike1/tpms/tpms1/availability", qos=1),
            ],
        )


if __name__ == "__main__":
    unittest.main()

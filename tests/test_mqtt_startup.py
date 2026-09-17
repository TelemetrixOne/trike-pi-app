from __future__ import annotations

import unittest
from unittest.mock import patch

from hpr_gateway.config import HprConfig
from hpr_gateway.mqtt import connect_client


def config() -> HprConfig:
    return HprConfig(
        {
            "hpr": {
                "trike_id": "trike1",
                "display_name": "Ashton",
                "topic_root": "hpr",
                "environment": "test",
            },
            "pi": {"hostname": "hpr-trike1"},
            "network": {
                "mqtt": {"host": "mqtt", "port": 1883, "username": "hpr", "password": "x"},
                "home_assistant": {"host": "ha", "port": 8123, "url": "http://ha:8123"},
            },
            "services": {},
        },
        "test.yaml",
    )


class MqttStartupTests(unittest.TestCase):
    @patch("hpr_gateway.mqtt.make_client")
    def test_last_will_is_set_before_connect(self, make_client) -> None:
        client = make_client.return_value
        connect_client(
            config(),
            "gps",
            will_topic="hpr/trike1/nav/status",
            will_payload="offline",
            will_qos=1,
            will_retain=True,
        )
        self.assertLess(
            client.method_calls.index(
                unittest.mock.call.will_set(
                    "hpr/trike1/nav/status",
                    payload="offline",
                    qos=1,
                    retain=True,
                )
            ),
            client.method_calls.index(unittest.mock.call.connect("mqtt", 1883, keepalive=60)),
        )


if __name__ == "__main__":
    unittest.main()

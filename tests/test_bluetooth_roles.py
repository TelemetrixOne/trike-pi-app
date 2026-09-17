from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from hpr_gateway.bluetooth import controller_map, resolve_bluetooth_role
from hpr_gateway.config import ConfigError, HprConfig


HCICONFIG = """hci1:   Type: Primary  Bus: USB
        BD Address: 5C:F3:70:A4:51:3D  ACL MTU: 1021:8  SCO MTU: 64:1
hci0:   Type: Primary  Bus: UART
        BD Address: 88:A2:9E:83:B0:2F  ACL MTU: 1021:8  SCO MTU: 64:1
"""


def minimal_config() -> HprConfig:
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
            "bluetooth": {
                "roles": {
                    "telemetry": {"controller_address": "88:A2:9E:83:B0:2F"},
                    "heart_rate": {"controller_address": "5C:F3:70:A4:51:3D"},
                }
            },
            "services": {},
        },
        "test.yaml",
    )


class BluetoothRoleTests(unittest.TestCase):
    @patch("subprocess.check_output", return_value=HCICONFIG)
    def test_controller_map_uses_stable_addresses(self, _mock) -> None:
        self.assertEqual(
            controller_map(),
            {"hci1": "5C:F3:70:A4:51:3D", "hci0": "88:A2:9E:83:B0:2F"},
        )

    @patch("hpr_gateway.bluetooth.controller_map")
    def test_role_survives_hci_renumbering(self, mapped) -> None:
        mapped.return_value = {
            "hci0": "5C:F3:70:A4:51:3D",
            "hci2": "88:A2:9E:83:B0:2F",
        }
        self.assertEqual(resolve_bluetooth_role(minimal_config(), "telemetry"), "hci2")
        self.assertEqual(resolve_bluetooth_role(minimal_config(), "heart_rate"), "hci0")

    @patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "hciconfig"))
    def test_enumeration_failure_is_explicit(self, _mock) -> None:
        with self.assertRaises(ConfigError):
            controller_map()


if __name__ == "__main__":
    unittest.main()

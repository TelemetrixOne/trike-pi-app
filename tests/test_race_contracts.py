from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from hpr_gateway.config import load_config
from hpr_gateway.services.tpms import decode_ai8000


ROOT = Path(__file__).resolve().parents[2]
TRIKE1_CONFIG = ROOT / "hpr-standalone-docker" / "generated" / "pi-configs" / "trike1" / "hpr.yaml"
MEDIAMTX = ROOT / "pi-gateway" / "vendor" / "mediamtx" / "linux_arm64" / "mediamtx"


class RaceContractTests(unittest.TestCase):
    def test_tpms_race_decoder_reports_psi(self) -> None:
        payload = bytearray(12)
        pressure_raw = round(80 * 27.025 - 9.5)
        payload[7:9] = pressure_raw.to_bytes(2, "little")
        payload[10:12] = (2350).to_bytes(2, "little")
        decoded = decode_ai8000(bytes(payload))
        self.assertIsNotNone(decoded)
        self.assertAlmostEqual(decoded["psi"], 80.0, delta=0.05)
        self.assertEqual(decoded["temperature_c"], 23.5)

    def test_rendered_trike1_hardware_contract(self) -> None:
        config = load_config(str(TRIKE1_CONFIG))
        self.assertEqual(config.trike_id, "trike1")
        self.assertEqual(config.get("bluetooth.roles.telemetry.controller_address"), "88:A2:9E:83:B0:2F")
        self.assertEqual(config.get("bluetooth.roles.heart_rate.controller_address"), "5C:F3:70:A4:51:3D")
        self.assertEqual(config.get("heart_rate.active_adapter"), "hci0")
        self.assertEqual(config.get("heart_rate.scan_adapter"), "hci1")
        self.assertTrue(config.get("gps.device").startswith("/dev/serial/by-id/"))
        self.assertEqual(config.get("tpms.sensors.tpms1.topic"), "{topic_root}/{trike_id}/tpms/tpms1")
        self.assertEqual(config.get("video.cameras.front.video_size"), "640x480")
        self.assertEqual(config.get("video.cameras.rear.video_size"), "640x480")

    def test_packaged_mediamtx_checksum(self) -> None:
        config = load_config(str(TRIKE1_CONFIG))
        digest = hashlib.sha256(MEDIAMTX.read_bytes()).hexdigest().upper()
        self.assertEqual(digest, config.get("video.mediamtx_sha256").upper())

    def test_hrm_identity_is_published_at_gatt_lock(self) -> None:
        source = inspect.getsource(heart_rate.connect_and_validate_candidate)
        identity_publish = source.index("publish_selected(mqttc, mac)")
        notification_start = source.index("client.start_notify")

        self.assertLess(identity_publish, notification_start)
        self.assertEqual(source.count("publish_selected(mqttc, mac)"), 1)

    def test_hrm_scans_and_connects_on_separate_controller_roles(self) -> None:
        source = inspect.getsource(heart_rate.configure)
        connect_source = inspect.getsource(heart_rate.connect_and_validate_candidate)

        self.assertIn('config, "telemetry", legacy_adapter_path="heart_rate.active_adapter"', source)
        self.assertIn('config, "heart_rate", legacy_adapter_path="heart_rate.scan_adapter"', source)
        self.assertIn("BleakScanner.find_device_by_address", connect_source)
        self.assertIn("adapter=ACTIVE_ADAPTER", connect_source)


if __name__ == "__main__":
    unittest.main()

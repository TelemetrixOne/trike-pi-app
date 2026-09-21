from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import unittest

import yaml

from hpr_gateway.config import ConfigError, HprConfig
from hpr_gateway.services import heart_rate
from hpr_gateway.services.tpms import decode_ai8000


ROOT = Path(__file__).resolve().parents[1]
TRIKE1_CONFIG = ROOT / "config" / "hpr.example.yaml"
MEDIAMTX = ROOT / "vendor" / "mediamtx" / "linux_arm64" / "mediamtx"
COMPOSITOR = ROOT / "bin" / "hpr-video-compose.sh"


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
        config = yaml.safe_load(TRIKE1_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["hpr"]["trike_id"], "trike1")
        self.assertEqual(config["bluetooth"]["roles"]["telemetry"]["fallback_adapter"], "hci0")
        self.assertEqual(config["bluetooth"]["roles"]["heart_rate"]["fallback_adapter"], "hci1")
        self.assertEqual(config["heart_rate"]["active_adapter"], "hci0")
        self.assertEqual(config["heart_rate"]["scan_adapter"], "hci1")
        self.assertEqual(config["tpms"]["sensors"]["tpms1"]["topic"], "{topic_root}/{trike_id}/tpms/tpms1")
        self.assertEqual(config["video"]["cameras"]["front"]["video_size"], "640x480")
        self.assertEqual(config["video"]["cameras"]["front"]["rotation"], "none")
        self.assertEqual(config["video"]["cameras"]["rear"]["video_size"], "640x480")
        self.assertEqual(config["video"]["display"]["capture_size"], "1920x1080")
        self.assertEqual(config["video"]["display"]["capture_framerate"], 30)

    def test_hdmi_compositor_uses_direct_camera_inputs(self) -> None:
        source = COMPOSITOR.read_text(encoding="utf-8")
        self.assertIn('v4l2src', source)
        self.assertIn('v4l2jpegdec', source)
        self.assertIn('glvideomixer', source)
        self.assertIn('glimagesink', source)
        self.assertIn('aspect-preserving crop', source)
        self.assertNotIn('rtsp://127.0.0.1:8554/${MAIN_PATH}" -i', source)

    def test_packaged_mediamtx_checksum(self) -> None:
        config = yaml.safe_load(TRIKE1_CONFIG.read_text(encoding="utf-8"))
        digest = hashlib.sha256(MEDIAMTX.read_bytes()).hexdigest().upper()
        self.assertEqual(digest, config["video"]["mediamtx_sha256"].upper())

    def test_invalid_camera_rotation_is_rejected(self) -> None:
        text = TRIKE1_CONFIG.read_text(encoding="utf-8").replace("CHANGE_ME", "127.0.0.1")
        config = yaml.safe_load(text)
        config["bluetooth"]["roles"]["telemetry"]["controller_address"] = "AA:BB:CC:DD:EE:01"
        config["bluetooth"]["roles"]["heart_rate"]["controller_address"] = "AA:BB:CC:DD:EE:02"
        config["services"]["heart_rate"]["enabled"] = False
        config["services"]["derailleur"]["enabled"] = False
        config["services"]["gpio_control"]["enabled"] = False
        config["video"]["cameras"]["front"]["rotation"] = "sideways"
        with self.assertRaisesRegex(ConfigError, "rotation"):
            HprConfig(config, str(TRIKE1_CONFIG))

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

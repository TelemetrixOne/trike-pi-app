import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml

from hpr_gateway.services.trike_config_sync import ProfileApplier, profile_hash


class TrikeConfigSyncTests(unittest.TestCase):
    def test_camera_location_is_merged_without_losing_encoder_settings(self):
        source = Path(__file__).resolve().parents[1] / "config" / "hpr.example.yaml"
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
        def replace_placeholders(value):
            if isinstance(value, dict):
                return {key: replace_placeholders(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_placeholders(item) for item in value]
            if isinstance(value, str):
                return value.replace("CHANGE_ME", "127.0.0.1")
            return value
        data = replace_placeholders(data)
        data["hpr"]["trike_id"] = "trike1"
        data["network"]["mqtt"].update({"host": "mqtt.example", "username": "hpr", "password": "secret"})
        data["bluetooth"]["roles"]["telemetry"]["controller_address"] = "AA:BB:CC:DD:EE:01"
        data["bluetooth"]["roles"]["heart_rate"]["controller_address"] = "AA:BB:CC:DD:EE:02"
        data["services"].setdefault("heart_rate", {})["enabled"] = False
        data["services"].setdefault("derailleur", {})["enabled"] = False
        data["services"].setdefault("gpio_control", {})["enabled"] = False
        data["video"]["cameras"]["front"]["device"] = "/dev/v4l/by-id/old-front"
        data["video"]["mediamtx_sha256"] = "0" * 64
        original_bitrate = data["video"]["cameras"]["front"]["bitrate"]
        profile = {
            "schema_version": 1, "trike_id": "trike1",
            "identity": {"display_name": "Project 646", "hostname": "hpr-trike1"},
            "heart_rate": {"enabled": False, "monitors": {}},
            "derailleur": {"enabled": False, "name": "", "mac": ""},
            "video": {"enabled": True, "cameras": {
                "front": {"enabled": True, "device": "/dev/v4l/by-path/new-front", "path": "front", "rotation": "anticlockwise_90"},
                "rear": {"enabled": False, "device": "/dev/v4l/by-path/new-rear", "path": "rear"},
            }},
            "tpms": {"tpms_enabled": False, "sensors": {}},
            "power_cadence": {"power_cadence_enabled": False, "devices": {}},
            "gpio": {"gpio_enabled": False, "gpio": {}},
        }
        envelope = {"hash": profile_hash(profile), "profile": profile}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hpr.yaml"
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            systemd = Mock()
            ProfileApplier(str(path), systemd).apply(copy.deepcopy(envelope))
            applied = yaml.safe_load(path.read_text(encoding="utf-8"))

        self.assertEqual(applied["video"]["cameras"]["front"]["device"], "/dev/v4l/by-path/new-front")
        self.assertEqual(applied["video"]["cameras"]["front"]["rotation"], "anticlockwise_90")
        self.assertEqual(applied["video"]["cameras"]["front"]["bitrate"], original_bitrate)
        self.assertEqual(applied["hpr"]["display_name"], "Project 646")
        self.assertEqual(applied["configuration"]["trike_hash"], envelope["hash"])


if __name__ == "__main__":
    unittest.main()

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

import yaml

from hpr_gateway.services.trike_config_sync import ProfileApplier, profile_hash


class TrikeConfigSyncTests(unittest.TestCase):
    @staticmethod
    def _valid_config() -> dict:
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
        data["derailleur"]["enabled"] = False
        data["video"]["cameras"]["front"]["device"] = "/dev/v4l/by-id/old-front"
        data["video"]["mediamtx_sha256"] = "0" * 64
        return data

    def test_centrally_managed_camera_settings_are_merged(self):
        data = self._valid_config()
        profile = {
            "schema_version": 1, "trike_id": "trike1",
            "identity": {"display_name": "Project 646", "hostname": "hpr-trike1"},
            "heart_rate": {"enabled": False, "monitors": {}},
            "derailleur": {"enabled": False, "name": "", "mac": ""},
            "video": {"enabled": True, "cameras": {
                "front": {"enabled": True, "device": "/dev/v4l/by-path/new-front", "path": "front", "rotation": "none",
                          "video_size": "1280x720", "stream_size": "640x360", "input_framerate": 30,
                          "output_framerate": 25, "bitrate": "900k", "gop": 25},
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
        self.assertEqual(applied["video"]["cameras"]["front"]["rotation"], "none")
        self.assertEqual(applied["video"]["cameras"]["front"]["video_size"], "1280x720")
        self.assertEqual(applied["video"]["cameras"]["front"]["stream_size"], "640x360")
        self.assertEqual(applied["video"]["cameras"]["front"]["bitrate"], "900k")
        self.assertEqual(applied["hpr"]["display_name"], "Project 646")
        self.assertEqual(applied["configuration"]["trike_hash"], envelope["hash"])

    def test_rotation_only_restarts_affected_camera(self):
        data = self._valid_config()
        data.setdefault("configuration", {})["trike_hash"] = "old"
        profile = {
            "schema_version": 1, "trike_id": "trike1",
            "identity": {"display_name": data["hpr"]["display_name"], "hostname": data["pi"]["hostname"]},
            "heart_rate": {"enabled": False, "monitors": data["heart_rate"]["monitors"]},
            "derailleur": {"enabled": False, "name": data["derailleur"]["device_name"], "mac": data["derailleur"]["mac"]},
            "video": {"enabled": True, "cameras": {
                "front": {"enabled": True, "device": data["video"]["cameras"]["front"]["device"], "path": "front", "rotation": "anticlockwise_90"},
                "rear": {"enabled": True, "device": data["video"]["cameras"]["rear"]["device"], "path": "rear", "rotation": "none"},
            }},
            "tpms": {"tpms_enabled": data["services"]["tpms"]["enabled"], "sensors": data["tpms"]["sensors"]},
            "power_cadence": {"power_cadence_enabled": data["services"]["power_cadence"]["enabled"], "devices": data["power_cadence"]["devices"]},
            "gpio": {"gpio_enabled": False, "gpio": data["gpio"]},
        }
        envelope = {"hash": profile_hash(profile), "profile": profile}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hpr.yaml"
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            systemd = Mock()
            ProfileApplier(str(path), systemd).apply(copy.deepcopy(envelope))

        systemd.apply.assert_called_once_with("hpr-video-front.service", True)

    def test_enabling_pip_switches_to_direct_compositor(self):
        data = self._valid_config()
        profile = {
            "schema_version": 1, "trike_id": "trike1",
            "identity": {"display_name": data["hpr"]["display_name"], "hostname": data["pi"]["hostname"]},
            "heart_rate": {"enabled": False, "monitors": data["heart_rate"]["monitors"]},
            "derailleur": {"enabled": False, "name": data["derailleur"]["device_name"], "mac": data["derailleur"]["mac"]},
            "video": {"enabled": True, "display": {
                "enabled": True, "camera": "front", "device": "/dev/fb0", "pixel_format": "rgb565le",
                "capture_size": "1920x1080", "capture_framerate": 30,
                "picture_in_picture": {"enabled": True, "camera": "rear", "width_percent": 25, "margin_pixels": 24},
            }, "cameras": {
                "front": {"enabled": True, "device": data["video"]["cameras"]["front"]["device"], "path": "front", "rotation": "anticlockwise_90"},
                "rear": {"enabled": True, "device": data["video"]["cameras"]["rear"]["device"], "path": "rear", "rotation": "none"},
            }},
            "tpms": {"tpms_enabled": data["services"]["tpms"]["enabled"], "sensors": data["tpms"]["sensors"]},
            "power_cadence": {"power_cadence_enabled": data["services"]["power_cadence"]["enabled"], "devices": data["power_cadence"]["devices"]},
            "gpio": {"gpio_enabled": False, "gpio": data["gpio"]},
        }
        envelope = {"hash": profile_hash(profile), "profile": profile}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hpr.yaml"
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            systemd = Mock()
            ProfileApplier(str(path), systemd).apply(copy.deepcopy(envelope))

        self.assertEqual(systemd.apply.call_args_list, [
            call("hpr-video-mediamtx.service", True),
            call("hpr-video-front.service", False),
            call("hpr-video-rear.service", False),
            call("hpr-video-compositor.service", True),
        ])


if __name__ == "__main__":
    unittest.main()

"""Atomically apply the complete centrally managed profile for this trike."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import queue
import signal
import subprocess
import tempfile
from pathlib import Path

import yaml

from ..config import HprConfig, config_arg_parser, load_config
from ..mqtt import make_client


LOG = logging.getLogger(__name__)
MAX_PROFILE_BYTES = 262144


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(json.dumps(
        profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode()).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    stat = path.stat()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, stat.st_mode & 0o777)
    if hasattr(os, "chown"):
        os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(temporary, path)


class Systemd:
    def apply(self, unit: str, enabled: bool) -> None:
        command = ["systemctl", "enable", unit] if enabled else ["systemctl", "disable", "--now", unit]
        subprocess.run(command, check=True, capture_output=True, timeout=45)
        if enabled:
            subprocess.run(["systemctl", "restart", unit], check=True, capture_output=True, timeout=45)


class ProfileApplier:
    def __init__(self, path: str, systemd: Systemd | None = None):
        self.path = Path(path)
        self.systemd = systemd or Systemd()
        self.backup = self.path.with_name(".trike-config-sync-backup.yaml")
        self.pending = self.path.with_name(".trike-config-sync-pending")

    def recover(self) -> None:
        if not self.pending.exists() or not self.backup.exists():
            return
        atomic_write(self.path, self.backup.read_bytes())
        self.pending.unlink(missing_ok=True)
        LOG.warning("Recovered interrupted central trike configuration change")

    @staticmethod
    def _merge_camera(target: dict, source: dict) -> None:
        for key in ("enabled", "device", "path"):
            if key in source:
                target[key] = source[key]

    def apply(self, envelope: dict) -> str:
        self.recover()
        current = load_config(str(self.path))
        profile = envelope.get("profile")
        if not isinstance(profile, dict) or profile.get("schema_version") != 1:
            raise ValueError("Unsupported or missing trike profile")
        if profile.get("trike_id") != current.trike_id:
            raise ValueError("Trike profile does not belong to this Pi")
        digest = profile_hash(profile)
        if envelope.get("hash") != digest:
            raise ValueError("Trike profile hash mismatch")
        if current.get("configuration.trike_hash") == digest:
            return digest

        candidate = copy.deepcopy(current.data)
        changed_units: dict[str, bool] = {}

        identity = profile.get("identity", {})
        candidate.setdefault("hpr", {})["display_name"] = identity.get("display_name", current.get("hpr.display_name"))
        candidate.setdefault("pi", {})["hostname"] = identity.get("hostname", current.get("pi.hostname"))

        heart_rate = profile.get("heart_rate", {})
        candidate.setdefault("heart_rate", {})["monitors"] = copy.deepcopy(heart_rate.get("monitors", {}))
        heart_enabled = bool(heart_rate.get("enabled"))
        candidate.setdefault("services", {}).setdefault("heart_rate", {})["enabled"] = heart_enabled
        if candidate.get("heart_rate") != current.get("heart_rate") or heart_enabled != current.enabled("heart_rate"):
            changed_units["hpr-heart-rate.service"] = heart_enabled

        derailleur = profile.get("derailleur", {})
        derail_target = candidate.setdefault("derailleur", {})
        derail_target["enabled"] = bool(derailleur.get("enabled"))
        derail_target["device_name"] = str(derailleur.get("name") or derailleur.get("device_name") or "")
        derail_target["mac"] = str(derailleur.get("mac") or "").upper()
        derail_enabled = derail_target["enabled"]
        candidate["services"].setdefault("derailleur", {})["enabled"] = derail_enabled
        if derail_target != current.get("derailleur") or derail_enabled != current.enabled("derailleur"):
            changed_units["hpr-derailleur.service"] = derail_enabled

        video = profile.get("video", {})
        video_target = candidate.setdefault("video", {})
        video_enabled = bool(video.get("enabled"))
        video_target["enabled"] = video_enabled
        for camera_name, camera in (video.get("cameras", {}) or {}).items():
            self._merge_camera(video_target.setdefault("cameras", {}).setdefault(camera_name, {}), camera)
        candidate["services"].setdefault("video", {})["enabled"] = video_enabled
        if video_target != current.get("video") or video_enabled != current.enabled("video"):
            changed_units["hpr-video-mediamtx.service"] = video_enabled
            for camera_name, camera in video_target.get("cameras", {}).items():
                changed_units[f"hpr-video-{camera_name}.service"] = video_enabled and bool(camera.get("enabled"))

        tpms = profile.get("tpms", {})
        candidate.setdefault("tpms", {})["sensors"] = copy.deepcopy(tpms.get("sensors", {}))
        tpms_enabled = bool(tpms.get("tpms_enabled"))
        candidate["services"].setdefault("tpms", {})["enabled"] = tpms_enabled
        if candidate["tpms"] != current.get("tpms") or tpms_enabled != current.enabled("tpms"):
            changed_units["hpr-tpms.service"] = tpms_enabled

        pedals = profile.get("power_cadence", {})
        candidate.setdefault("power_cadence", {})["devices"] = copy.deepcopy(pedals.get("devices", {}))
        pedals_enabled = bool(pedals.get("power_cadence_enabled"))
        candidate["services"].setdefault("power_cadence", {})["enabled"] = pedals_enabled
        if candidate["power_cadence"] != current.get("power_cadence") or pedals_enabled != current.enabled("power_cadence"):
            changed_units["hpr-power-cadence.service"] = pedals_enabled

        gpio = profile.get("gpio", {})
        candidate["gpio"] = copy.deepcopy(gpio.get("gpio", {}))
        gpio_enabled = bool(gpio.get("gpio_enabled"))
        candidate["services"].setdefault("gpio_control", {})["enabled"] = gpio_enabled
        if candidate["gpio"] != current.get("gpio") or gpio_enabled != current.enabled("gpio_control"):
            changed_units["hpr-gpio-control.service"] = gpio_enabled

        candidate.setdefault("configuration", {})["trike_hash"] = digest
        HprConfig(candidate, str(self.path))
        self.backup.write_bytes(self.path.read_bytes())
        self.backup.chmod(0o600)
        self.pending.write_text(digest, encoding="ascii")
        try:
            atomic_write(self.path, yaml.safe_dump(candidate, sort_keys=False).encode())
            for unit, enabled in changed_units.items():
                self.systemd.apply(unit, enabled)
        except Exception:
            atomic_write(self.path, self.backup.read_bytes())
            raise
        finally:
            self.pending.unlink(missing_ok=True)
        return digest


def main() -> int:
    args = config_arg_parser("Synchronise centrally managed trike configuration").parse_args()
    applier = ProfileApplier(args.config)
    applier.recover()
    config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    desired = config.topic("config", "trike", "desired")
    status = config.topic("config", "trike", "status")
    messages: queue.Queue[bytes] = queue.Queue(maxsize=1)
    stopping = False
    client = make_client(config, f"hpr-{config.trike_id}-trike-config")

    def on_connect(connected, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            connected.subscribe(desired, qos=1)
            connected.publish(status, json.dumps({"state": "waiting", "trike_id": config.trike_id}), qos=1, retain=True)

    def on_message(_client, _userdata, message):
        if message.topic == desired and message.payload and len(message.payload) <= MAX_PROFILE_BYTES:
            if messages.full():
                messages.get_nowait()
            messages.put_nowait(message.payload)

    def stop(*_args):
        nonlocal stopping
        stopping = True

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.mqtt.host, config.mqtt.port, keepalive=30)
    client.loop_start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                envelope = json.loads(messages.get(timeout=1))
                result = {"state": "applied", "hash": applier.apply(envelope), "trike_id": config.trike_id}
            except queue.Empty:
                continue
            except Exception as exc:
                LOG.exception("Central trike configuration rejected or rolled back")
                result = {"state": "error", "error": str(exc), "trike_id": config.trike_id}
            client.publish(status, json.dumps(result), qos=1, retain=True)
    finally:
        client.disconnect()
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

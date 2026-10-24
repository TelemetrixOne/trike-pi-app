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
import threading
import time
import uuid
from pathlib import Path

import yaml

from ..config import HprConfig, config_arg_parser, load_config
from ..mqtt import make_client
from ..config_transaction import migrated, serialized_config


LOG = logging.getLogger(__name__)
MAX_PROFILE_BYTES = 262144
SECTIONS = ('identity', 'heart_rate', 'derailleur', 'video', 'tpms', 'power_cadence', 'gpio')
LEGACY_UNITS = ('hpr-gpio-config-sync.service', 'hpr-tpms-config-sync.service',
                'hpr-power-cadence-config-sync.service')


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
    if hasattr(os, 'O_DIRECTORY'):
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Systemd:
    def active(self, unit):
        return subprocess.run(['systemctl', 'is-active', '--quiet', unit], timeout=10).returncode == 0

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

    @serialized_config()
    def recover(self) -> None:
        if not self.pending.exists():
            return
        if not self.backup.exists():
            raise RuntimeError('Interrupted config transaction has no backup; preserve and investigate')
        transaction = json.loads(self.pending.read_text(encoding='utf-8'))
        atomic_write(self.path, self.backup.read_bytes())
        for unit, enabled in transaction['previous_units'].items():
            self.systemd.apply(unit, enabled)
        self.pending.unlink(missing_ok=True)
        LOG.warning("Recovered interrupted central trike configuration change")

    @staticmethod
    def _merge_camera(target: dict, source: dict) -> None:
        for key in (
            "enabled", "device", "path", "input_format", "video_size",
            "stream_size", "input_framerate", "output_framerate", "bitrate",
            "gop", "rotation",
        ):
            if key in source:
                target[key] = source[key]

    def prepare(self, current, envelope):
        profile = envelope.get("profile")
        if not isinstance(profile, dict) or profile.get("schema_version") != 1:
            raise ValueError("Unsupported or missing trike profile")
        if profile.get("trike_id") != current.trike_id:
            raise ValueError("Trike profile does not belong to this Pi")
        digest = profile_hash(profile)
        if envelope.get("hash") != digest:
            raise ValueError("Trike profile hash mismatch")
        if not all(isinstance(profile.get(section), dict) for section in SECTIONS):
            raise ValueError('Complete trike profile required; missing managed section')
        for section, key in (('heart_rate', 'enabled'), ('derailleur', 'enabled'),
                             ('video', 'enabled'), ('tpms', 'tpms_enabled'),
                             ('power_cadence', 'power_cadence_enabled'), ('gpio', 'gpio_enabled')):
            if type(profile[section].get(key)) is not bool:
                raise ValueError('Missing or invalid enabled flag for ' + section)

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
        previous_video = copy.deepcopy(current.get("video", {}) or {})
        video_enabled = bool(video.get("enabled"))
        video_target["enabled"] = video_enabled
        if isinstance(video.get("display"), dict):
            video_target["display"] = copy.deepcopy(video["display"])
        for camera_name, camera in (video.get("cameras", {}) or {}).items():
            self._merge_camera(video_target.setdefault("cameras", {}).setdefault(camera_name, {}), camera)
        candidate["services"].setdefault("video", {})["enabled"] = video_enabled
        if video_target != current.get("video") or video_enabled != current.enabled("video"):
            previous_without_rotation = copy.deepcopy(previous_video)
            target_without_rotation = copy.deepcopy(video_target)
            rotation_changed = set()
            for camera_name, camera in video_target.get("cameras", {}).items():
                previous_camera = previous_video.get("cameras", {}).get(camera_name, {})
                if camera.get("rotation", "none") != previous_camera.get("rotation", "none"):
                    rotation_changed.add(camera_name)
                previous_without_rotation.get("cameras", {}).get(camera_name, {}).pop("rotation", None)
                target_without_rotation.get("cameras", {}).get(camera_name, {}).pop("rotation", None)
            rotation_only = (
                rotation_changed
                and previous_without_rotation == target_without_rotation
                and video_enabled == current.enabled("video")
            )
            pip_enabled = bool(video_target.get("display", {}).get("picture_in_picture", {}).get("enabled"))
            native_video = (Path(__file__).resolve().parents[2] / "runtime/video/hpr-video-native.py").is_file()
            if native_video:
                # The supervisor owns both publishers with or without HDMI.
                # Retired PiP settings must not resurrect competing USB owners.
                if video_enabled != current.enabled("video"):
                    changed_units["hpr-video-mediamtx.service"] = video_enabled
                for camera_name in video_target.get("cameras", {}):
                    changed_units[f"hpr-video-{camera_name}.service"] = False
                changed_units["hpr-video-compositor.service"] = video_enabled
            elif rotation_only and not pip_enabled:
                for camera_name in rotation_changed:
                    camera = video_target.get("cameras", {}).get(camera_name, {})
                    changed_units[f"hpr-video-{camera_name}.service"] = video_enabled and bool(camera.get("enabled"))
            else:
                changed_units["hpr-video-mediamtx.service"] = video_enabled
                if pip_enabled:
                    for camera_name in video_target.get("cameras", {}):
                        changed_units[f"hpr-video-{camera_name}.service"] = False
                    changed_units["hpr-video-compositor.service"] = video_enabled
                else:
                    changed_units["hpr-video-compositor.service"] = False
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
        return candidate, changed_units

    def _previous_enabled(self, config, unit):
        name = unit.removeprefix('hpr-').removesuffix('.service')
        if name.startswith('video-'):
            # Legacy camera units must NOT be resurrected on native-owner rollback.
            return bool(self.systemd.active(unit))
        return config.enabled(name.replace('-', '_'))

    @serialized_config()
    def apply(self, envelope: dict) -> str:
        self.recover()
        current = load_config(str(self.path))
        candidate, changed_units = self.prepare(current, envelope)
        digest = envelope['hash']
        if candidate == current.data:
            return digest
        atomic_journal = self.pending
        # Journal is durable before replacing the config; all writers share lock.
        from .gpio_config_sync import atomic_write as write_journal
        write_journal(self.backup, self.path.read_bytes())
        write_journal(atomic_journal, json.dumps({'previous_units': {
            unit: self._previous_enabled(current, unit) for unit in changed_units}}).encode())
        try:
            atomic_write(self.path, yaml.safe_dump(candidate, sort_keys=False).encode())
            for unit, enabled in changed_units.items():
                self.systemd.apply(unit, enabled)
        except Exception:
            self.recover()
            raise
        self.pending.unlink(missing_ok=True)
        return digest


class SyncEvidence:
    """Fresh capability plus read-back evidence; no GPIO or actuator operations."""
    def __init__(self, applier):
        self.applier = applier
        self.boot_id, self.seq = str(uuid.uuid4()), 0
        self.envelope = None
        self.error = None

    def status(self):
        self.seq += 1
        single = False
        try:
            single = migrated(self.applier.path) and subprocess.run(
                ['systemctl', 'is-active', '--quiet', *LEGACY_UNITS],
                capture_output=True, timeout=2).returncode == 3
        except (OSError, subprocess.SubprocessError):
            pass
        result = {'schema_version': 2, 'config_sync_version': 2, 'owner': 'trike-config-sync',
                  'single_writer': single, 'boot_id': self.boot_id, 'seq': self.seq,
                  'published_at': time.time(), 'state': 'waiting', 'hash': None,
                  'section_hashes': {}, 'trike_id': load_config(str(self.applier.path)).trike_id}
        if self.envelope:
            result['hash'] = self.envelope['hash']
            try:
                current = load_config(str(self.applier.path))
                candidate, _ = self.applier.prepare(current, self.envelope)
                result['state'] = 'applied' if candidate == current.data else 'drift'
                if result['state'] == 'applied':
                    result['section_hashes'] = {
                        section: profile_hash(self.envelope['profile'][section]) for section in SECTIONS}
            except Exception as exc:
                result['state'], result['error'] = 'error', str(exc)[:240]
        if self.error:
            result['state'], result['error'] = 'error', self.error
            result['section_hashes'] = {}
        if not single and migrated(self.applier.path):
            result['state'], result['error'] = 'error', 'Cannot verify standalone writers inactive'
            result['section_hashes'] = {}
        return result


def main() -> int:
    args = config_arg_parser("Synchronise centrally managed trike configuration").parse_args()
    applier = ProfileApplier(args.config)
    applier.recover()
    config = load_config(args.config)
    evidence = SyncEvidence(applier)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    desired = config.topic("config", "trike", "desired")
    status = config.topic("config", "trike", "status")
    messages: queue.Queue[bytes] = queue.Queue(maxsize=1)
    stopping = False
    client = make_client(config, f"hpr-{config.trike_id}-trike-config")
    client.max_queued_messages_set(1)
    connected_event = threading.Event()

    def on_connect(connected, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            connected_event.set()
            connected.subscribe(desired, qos=1)

    def on_disconnect(*_args):
        connected_event.clear()

    def on_message(_client, _userdata, message):
        if message.topic == desired and message.payload and len(message.payload) <= MAX_PROFILE_BYTES:
            if messages.full():
                messages.get_nowait()
            messages.put_nowait(message.payload)

    def stop(*_args):
        nonlocal stopping
        stopping = True

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(config.mqtt.host, config.mqtt.port, keepalive=30)
    client.loop_start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_report = 0
    try:
        while not stopping:
            try:
                envelope = json.loads(messages.get(timeout=1))
                applier.apply(envelope)
                evidence.envelope, evidence.error = envelope, None
                next_report = 0
            except queue.Empty:
                pass
            except Exception as exc:
                LOG.exception("Central trike configuration rejected or rolled back")
                evidence.error = str(exc)[:240]
                next_report = 0
            if connected_event.is_set() and time.monotonic() >= next_report:
                client.publish(status, json.dumps(evidence.status()), qos=1, retain=True)
                next_report = time.monotonic() + 5
    finally:
        client.disconnect()
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

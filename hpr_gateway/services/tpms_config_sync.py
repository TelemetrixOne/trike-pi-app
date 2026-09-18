"""Apply centrally managed TPMS assignments with an atomic rollback path."""

import copy
import json
import logging
import queue
import signal
import subprocess
import time
from pathlib import Path

import yaml

from ..config import HprConfig, config_arg_parser, load_config
from ..mqtt import make_client
from ..tpms_profile import profile_hash, validate_profile
from .gpio_config_sync import atomic_write


LOG = logging.getLogger(__name__)
UNIT = "hpr-tpms.service"


class TpmsSystemd:
    def apply(self, enabled):
        if enabled:
            subprocess.run(["systemctl", "enable", UNIT], check=True, capture_output=True, timeout=45)
            subprocess.run(["systemctl", "restart", UNIT], check=True, capture_output=True, timeout=45)
        else:
            subprocess.run(["systemctl", "disable", "--now", UNIT], check=True, capture_output=True, timeout=45)


class ProfileApplier:
    def __init__(self, path):
        self.path = Path(path)
        self.systemd = TpmsSystemd()
        self.pending = self.path.with_name(".tpms-sync-pending.json")
        self.history = self.path.parent / "tpms-history"

    def _write(self, content):
        stat = self.path.stat()
        atomic_write(self.path, content, stat.st_mode & 0o777, (stat.st_uid, stat.st_gid))

    def recover(self):
        if not self.pending.exists():
            return
        record = json.loads(self.pending.read_text(encoding="utf-8"))
        self._write((self.history / record["backup"]).read_bytes())
        self.systemd.apply(record["previous_enabled"])
        self.pending.unlink()
        LOG.warning("Recovered interrupted TPMS configuration change")

    def apply(self, envelope):
        self.recover()
        current = load_config(str(self.path))
        profile = envelope.get("profile")
        validate_profile(profile, current.trike_id)
        digest = profile_hash(profile)
        if envelope.get("hash") != digest:
            raise ValueError("TPMS profile hash mismatch")
        enabled = profile["tpms_enabled"]
        same = current.get("tpms.sensors") == profile["sensors"] and current.enabled("tpms") == enabled
        if same:
            return digest
        candidate = copy.deepcopy(current.data)
        candidate.setdefault("tpms", {})["sensors"] = profile["sensors"]
        candidate.setdefault("services", {}).setdefault("tpms", {})["enabled"] = enabled
        candidate.setdefault("configuration", {})["tpms_hash"] = digest
        HprConfig(candidate, str(self.path))
        self.history.mkdir(mode=0o700, exist_ok=True)
        backup = self.history / f"{time.time_ns()}-{digest[:12]}.yaml"
        backup.write_bytes(self.path.read_bytes())
        backup.chmod(0o600)
        atomic_write(self.pending, json.dumps({"backup": backup.name,
                     "previous_enabled": current.enabled("tpms")}).encode())
        try:
            self._write(yaml.safe_dump(candidate, sort_keys=False).encode())
            self.systemd.apply(enabled)
        except Exception:
            self.recover()
            raise
        self.pending.unlink()
        return digest


def main():
    args = config_arg_parser("Synchronise centrally managed TPMS assignments").parse_args()
    applier = ProfileApplier(args.config)
    applier.recover()
    config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    if not config.enabled("tpms_config_sync"):
        return 0
    desired = config.topic("config", "tpms", "desired")
    status = config.topic("config", "tpms", "status")
    messages = queue.Queue(maxsize=1)
    stopping = False
    client = make_client(config, f"hpr-{config.trike_id}-tpms-config")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(desired, qos=1)
            client.publish(status, json.dumps({"state": "waiting", "trike_id": config.trike_id}), qos=1, retain=True)

    def on_message(client, userdata, message):
        if message.topic == desired and message.payload and len(message.payload) <= 65536:
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
                raw = messages.get(timeout=1)
                envelope = json.loads(raw)
                digest = applier.apply(envelope)
                result = {"state": "applied", "hash": digest, "trike_id": config.trike_id}
            except queue.Empty:
                continue
            except Exception as exc:
                LOG.exception("TPMS update rejected or rolled back")
                result = {"state": "error", "error": str(exc), "trike_id": config.trike_id}
            client.publish(status, json.dumps(result), qos=1, retain=True)
    finally:
        client.disconnect()
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

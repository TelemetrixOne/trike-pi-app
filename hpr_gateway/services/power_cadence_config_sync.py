"""Apply centrally managed pedal-power assignments with rollback."""

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
from ..power_cadence_profile import profile_hash, validate_profile
from .gpio_config_sync import atomic_write


LOG = logging.getLogger(__name__)
UNIT = "hpr-power-cadence.service"


class ProfileApplier:
    def __init__(self, path):
        self.path = Path(path)
        self.pending = self.path.with_name(".power-cadence-sync-pending.json")
        self.history = self.path.parent / "power-cadence-history"

    def _write(self, content):
        stat = self.path.stat()
        atomic_write(self.path, content, stat.st_mode & 0o777, (stat.st_uid, stat.st_gid))

    def _service(self, enabled):
        subprocess.run(["systemctl", "enable", UNIT] if enabled else ["systemctl", "disable", "--now", UNIT], check=True, capture_output=True, timeout=45)
        if enabled:
            subprocess.run(["systemctl", "restart", UNIT], check=True, capture_output=True, timeout=45)

    def recover(self):
        if not self.pending.exists(): return
        record = json.loads(self.pending.read_text())
        self._write((self.history / record["backup"]).read_bytes())
        self._service(record["previous_enabled"])
        self.pending.unlink()

    def apply(self, envelope):
        self.recover(); current = load_config(str(self.path)); profile = envelope.get("profile")
        validate_profile(profile, current.trike_id); digest = profile_hash(profile)
        if envelope.get("hash") != digest: raise ValueError("Pedal-power profile hash mismatch")
        enabled = profile["power_cadence_enabled"]
        if current.get("power_cadence.devices") == profile["devices"] and current.enabled("power_cadence") == enabled: return digest
        candidate = copy.deepcopy(current.data)
        candidate.setdefault("power_cadence", {})["devices"] = profile["devices"]
        candidate.setdefault("services", {}).setdefault("power_cadence", {})["enabled"] = enabled
        candidate.setdefault("configuration", {})["power_cadence_hash"] = digest
        HprConfig(candidate, str(self.path)); self.history.mkdir(mode=0o700, exist_ok=True)
        backup = self.history / f"{time.time_ns()}-{digest[:12]}.yaml"; backup.write_bytes(self.path.read_bytes()); backup.chmod(0o600)
        atomic_write(self.pending, json.dumps({"backup": backup.name, "previous_enabled": current.enabled("power_cadence")}).encode())
        try:
            self._write(yaml.safe_dump(candidate, sort_keys=False).encode()); self._service(enabled)
        except Exception:
            self.recover(); raise
        self.pending.unlink(); return digest


def main():
    args = config_arg_parser("Synchronise centrally managed pedal-power assignments").parse_args(); applier = ProfileApplier(args.config); applier.recover(); config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    if not config.enabled("power_cadence_config_sync"): return 0
    desired, status = config.topic("config", "power_cadence", "desired"), config.topic("config", "power_cadence", "status")
    messages = queue.Queue(maxsize=1); stopping = False; client = make_client(config, f"hpr-{config.trike_id}-power-cadence-config")
    def connected(client, userdata, flags, reason_code, properties):
        if reason_code == 0: client.subscribe(desired, qos=1)
    def message(client, userdata, item):
        if item.topic == desired and item.payload and len(item.payload) <= 65536:
            if messages.full(): messages.get_nowait()
            messages.put_nowait(item.payload)
    def stop(*_args):
        nonlocal stopping; stopping = True
    client.on_connect, client.on_message = connected, message; client.connect_async(config.mqtt.host, config.mqtt.port, keepalive=30); client.loop_start(); signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try: envelope = json.loads(messages.get(timeout=1)); result = {"state": "applied", "hash": applier.apply(envelope), "trike_id": config.trike_id}
            except queue.Empty: continue
            except Exception as exc: LOG.exception("Pedal-power update rejected or rolled back"); result = {"state": "error", "error": str(exc), "trike_id": config.trike_id}
            client.publish(status, json.dumps(result), qos=1, retain=True)
    finally:
        client.disconnect(); client.loop_stop()


if __name__ == "__main__": raise SystemExit(main())

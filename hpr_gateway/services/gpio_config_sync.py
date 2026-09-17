"""Apply only GPIO settings from the authenticated race broker, with rollback."""

import copy
import json
import logging
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import time

import yaml

from ..config import HprConfig, config_arg_parser, load_config
from ..gpio_profile import profile_hash, validate_profile
from ..mqtt import make_client


LOG = logging.getLogger(__name__)
UNIT = "hpr-gpio-control.service"


def atomic_write(path, content, mode=0o600, owner=None):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if owner and hasattr(os, "chown"):
            os.chown(temporary, *owner)
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GpioSystemd:
    def command(self, *args):
        return subprocess.run(["systemctl", *args, UNIT], check=True,
                              capture_output=True, timeout=45)

    def active(self):
        result = subprocess.run(["systemctl", "is-active", "--quiet", UNIT], timeout=10)
        return result.returncode == 0

    def apply(self, enabled):
        if enabled:
            self.command("enable")
            # Type=notify returns only after GPIO devices have been configured.
            self.command("restart")
            if not self.active():
                raise RuntimeError("GPIO service did not become ready")
        else:
            self.command("disable", "--now")


class ProfileApplier:
    def __init__(self, path, controller=None):
        self.path = Path(path)
        self.controller = controller or GpioSystemd()
        self.pending = self.path.with_name(".gpio-sync-pending.json")
        self.history = self.path.parent / "gpio-history"

    def _write_config(self, content):
        stat = self.path.stat()
        atomic_write(self.path, content, stat.st_mode & 0o777, (stat.st_uid, stat.st_gid))

    def recover(self):
        if not self.pending.exists():
            return
        transaction = json.loads(self.pending.read_text(encoding="utf-8"))
        backup = self.history / Path(transaction["backup"]).name
        self._write_config(backup.read_bytes())
        self.controller.apply(transaction["previous_enabled"])
        self.pending.unlink()
        LOG.warning("Recovered interrupted GPIO config change")

    def apply(self, envelope):
        self.recover()
        current = load_config(str(self.path))
        profile = envelope.get("profile")
        validate_profile(profile, current.trike_id)
        digest = profile_hash(profile)
        if envelope.get("hash") != digest:
            raise ValueError("GPIO profile hash mismatch")
        enabled = profile["gpio_enabled"]
        previous_enabled = current.enabled("gpio_control")
        same = current.get("gpio") == profile["gpio"] and previous_enabled == enabled
        if same:
            if enabled != self.controller.active():
                self.controller.apply(enabled)
            return digest
        candidate = copy.deepcopy(current.data)
        candidate["gpio"] = profile["gpio"]
        candidate.setdefault("services", {}).setdefault("gpio_control", {})["enabled"] = enabled
        candidate.setdefault("configuration", {})["gpio_hash"] = digest
        HprConfig(candidate, str(self.path))
        original = self.path.read_bytes()
        self.history.mkdir(mode=0o700, exist_ok=True)
        backup = self.history / f"{time.time_ns()}-{digest[:12]}.yaml"
        with backup.open("xb") as handle:
            os.chmod(backup, 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write(self.pending, json.dumps({"backup": backup.name,
                     "previous_enabled": previous_enabled}).encode())
        try:
            self._write_config(yaml.safe_dump(candidate, sort_keys=False).encode())
            self.controller.apply(enabled)
        except Exception:
            self.recover()
            raise
        self.pending.unlink()
        return digest


def main():
    args = config_arg_parser("Synchronise centrally managed GPIO settings").parse_args()
    applier = ProfileApplier(args.config)
    applier.recover()
    config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    if not config.enabled("gpio_config_sync"):
        return 0
    desired = config.topic("config", "gpio", "desired")
    status = config.topic("config", "gpio", "status")
    messages = queue.Queue(maxsize=1)
    stopping = False
    client = make_client(config, f"hpr-{config.trike_id}-gpio-config")
    client.will_set(status, json.dumps({"state": "offline"}), qos=1, retain=True)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(desired, qos=1)
            client.publish(status, json.dumps({"state": "waiting", "trike_id": config.trike_id}), qos=1, retain=True)

    def on_message(client, userdata, message):
        if message.topic != desired or not message.payload or len(message.payload) > 65536:
            return
        if messages.full():
            try:
                messages.get_nowait()
            except queue.Empty:
                pass
        messages.put_nowait(message.payload)

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(config.mqtt.host, config.mqtt.port, keepalive=30)
    client.loop_start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = {"state": "waiting", "trike_id": config.trike_id}
    next_report = 0
    report_interval = max(1, float(config.get("services.gpio_config_sync.status_interval_seconds", 10)))
    try:
        while not stopping:
            try:
                raw = messages.get(timeout=1)
            except queue.Empty:
                if time.monotonic() >= next_report:
                    if result.get("state") == "applied":
                        try:
                            current = load_config(args.config)
                            actual = {"schema_version": 1, "trike_id": current.trike_id,
                                      "gpio_enabled": current.enabled("gpio_control"), "gpio": current.get("gpio")}
                            if profile_hash(actual) != result["hash"] or current.enabled("gpio_control") != applier.controller.active():
                                result = {"state": "drift", "trike_id": config.trike_id}
                        except Exception:
                            result = {"state": "error", "error": "Unable to verify applied GPIO configuration", "trike_id": config.trike_id}
                    result["reported_at"] = time.time()
                    client.publish(status, json.dumps(result), qos=1, retain=True)
                    next_report = time.monotonic() + report_interval
                continue
            requested_hash = None
            try:
                envelope = json.loads(raw)
                if not isinstance(envelope, dict):
                    raise ValueError("GPIO update must be an object")
                requested_hash = envelope.get("hash")
                digest = applier.apply(envelope)
                result = {"state": "applied", "hash": digest, "trike_id": config.trike_id}
            except Exception as exc:
                LOG.exception("GPIO update rejected or rolled back")
                result = {"state": "error", "hash": requested_hash, "error": str(exc), "trike_id": config.trike_id}
            result["reported_at"] = time.time()
            client.publish(status, json.dumps(result), qos=1, retain=True)
            next_report = time.monotonic() + report_interval
    finally:
        client.publish(status, json.dumps({"state": "offline"}), qos=1, retain=True)
        client.disconnect()
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

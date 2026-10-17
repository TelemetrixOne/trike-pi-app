"""Bounded GPS-only recovery, independent of MQTT connectivity and GPS publisher."""
from __future__ import annotations

import json
import logging
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import make_client
from hpr_gateway.services.gps import parse_gps_time, valid_position

LOG = logging.getLogger("hpr_gps_recovery")
LOSS_SECONDS = 10.0
REACQUIRE_SECONDS = 20.0
COOLDOWN_SECONDS = 60.0
BOOT_GRACE_SECONDS = 60.0
STATE_FILE = Path("/var/lib/hpr-gps-recovery/last-attempt.json")


@dataclass
class RecoveryPolicy:
    started: float
    last_attempt: float | None = None
    last_fix: float | None = None
    waiting_until: float | None = None
    state: str = "acquiring"
    startup_grace: float = BOOT_GRACE_SECONDS

    def observe(self, report: dict, now: float, wall: datetime) -> None:
        if report.get("class") == "TPV" and valid_position(report, wall, 5):
            # Repeated old TPVs cannot extend the last-fix clock.
            age = max(0.0, (wall - parse_gps_time(report["time"])).total_seconds())
            observed_fix = now - age
            self.last_fix = max(self.last_fix if self.last_fix is not None else observed_fix, observed_fix)
            if self.waiting_until is not None and observed_fix <= self.last_attempt:
                return
            self.waiting_until = None
            self.state = "healthy"

    def tick(self, now: float) -> bool:
        if self.waiting_until is not None:
            if now >= self.waiting_until:
                self.waiting_until = None
                self.state = "failed"
            return False
        lost = (now - self.last_fix >= LOSS_SECONDS if self.last_fix is not None
                else now - self.started >= self.startup_grace)
        if not lost:
            return False
        if self.last_attempt is not None and now - self.last_attempt < COOLDOWN_SECONDS:
            if self.state == "healthy":
                self.state = "cooldown"
            return False
        self.last_attempt = now
        self.waiting_until = now + REACQUIRE_SECONDS
        self.state = "recovering"
        return True


def load_last_attempt(path: Path, monotonic: float, wall: float) -> float | None:
    try:
        stamp = float(json.loads(path.read_text())["attempted_at_unix"])
        # Backwards wall-clock steps must not bypass the cooldown.
        return monotonic - min(COOLDOWN_SECONDS, max(0.0, wall - stamp))
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
        LOG.warning("Invalid recovery timestamp; applying full cooldown")
        return monotonic


def save_attempt(path: Path, wall: float) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps({"attempted_at_unix": wall}))
    pending.replace(path)


def restart_gps() -> None:
    # Fixed allowlist: never reset USB, Bluetooth or the Pi. Non-blocking jobs
    # keep the observer alive during restart; systemd orders the GPS dependency.
    subprocess.run(["systemctl", "--no-block", "restart", "gpsd.service", "hpr-gps.service"],
                   check=True, timeout=3, capture_output=True, text=True)


def capture_diagnostics(latest: dict, policy: RecoveryPolicy, now: float) -> None:
    evidence = {"last_fix_age_seconds": None if policy.last_fix is None else now - policy.last_fix,
                "latest_tpv": latest}
    try:
        result = subprocess.run(["journalctl", "-u", "gpsd.service", "-u", "hpr-gps.service",
                                 "--since=-2min", "--no-pager", "-n", "20", "-o", "cat"],
                                capture_output=True, text=True, timeout=2)
        evidence["recent_logs"] = result.stdout[-4000:]
    except (OSError, subprocess.SubprocessError) as exc:
        evidence["diagnostic_error"] = type(exc).__name__
    LOG.warning("GPS recovery evidence: %s", json.dumps(evidence, separators=(",", ":")))


def main() -> int:
    args = config_arg_parser("Recover persistent GPS fix loss without rebooting").parse_args()
    cfg = load_config(args.config)
    if not cfg.get("services.gps.enabled", False) or not cfg.get("gps.recovery_enabled", True):
        return 0
    logging.basicConfig(level=logging.INFO)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    started = time.monotonic()
    # Linux monotonic time starts at boot. Do not grant another minute merely
    # because the watchdog itself was restarted during racing.
    policy = RecoveryPolicy(started, startup_grace=max(LOSS_SECONDS, BOOT_GRACE_SECONDS - started))
    policy.last_attempt = load_last_attempt(STATE_FILE, policy.started, time.time())
    topic = cfg.resolve_topic(cfg.get("gps.recovery_topic"), "nav", "recovery")
    client = make_client(cfg, f"hpr-{cfg.trike_id}-gps-recovery")
    client.max_queued_messages_set(20)
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    client.will_set(topic, json.dumps({"state": "offline"}), qos=1, retain=True)
    client.connect_async(cfg.mqtt.host, cfg.mqtt.port, keepalive=30)
    client.loop_start()
    connection = None
    buffer = b""
    latest = {}
    next_connect = next_status = 0.0
    previous_state = None
    LOG.info("GPS recovery armed: loss=10s reacquire=20s cooldown=60s cold-start grace=60s")
    try:
        while not stopping:
            now = time.monotonic()
            if policy.tick(now):
                # Persist before acting, so a watchdog restart cannot cause a
                # rapid restart loop. Failed persistence prevents the action.
                try:
                    save_attempt(STATE_FILE, time.time())
                    capture_diagnostics(latest, policy, now)
                    restart_gps()
                    LOG.warning("GPS-only service recovery requested; allowing 20s to reacquire")
                except (OSError, subprocess.SubprocessError) as exc:
                    policy.waiting_until = None
                    policy.state = "failed"
                    LOG.error("GPS recovery request failed: %s", exc)
            if now >= next_status or policy.state != previous_state:
                if policy.state == "failed" and previous_state != "failed":
                    LOG.error("GPS recovery unsuccessful; cooldown remains active")
                elif policy.state == "healthy" and previous_state in {"recovering", "failed"}:
                    LOG.info("GPS position fix recovered")
                client.publish(topic, json.dumps({"state": policy.state,
                               "last_fix_age_seconds": None if policy.last_fix is None else now - policy.last_fix,
                               "cooldown_remaining_seconds": 0 if policy.last_attempt is None else
                               max(0, COOLDOWN_SECONDS - (now - policy.last_attempt)),
                               "observed_at": datetime.now(timezone.utc).isoformat()}), qos=1, retain=True)
                previous_state = policy.state
                next_status = now + 5
            try:
                if connection is None:
                    if now < next_connect:
                        time.sleep(0.2)
                        continue
                    next_connect = now + 1
                    connection = socket.create_connection((cfg.get("gps.gpsd_host", "127.0.0.1"),
                                                           int(cfg.get("gps.gpsd_port", 2947))), timeout=0.5)
                    connection.settimeout(0.5)
                    connection.sendall(b'?WATCH={"enable":true,"json":true};\n')
                    buffer = b""
                chunk = connection.recv(65536)
                if not chunk:
                    raise ConnectionError("gpsd closed observer socket")
                buffer += chunk
                if len(buffer) > 1024 * 1024:
                    raise ValueError("gpsd observer buffer limit")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        report = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(report, dict):
                        if report.get("class") == "TPV":
                            latest = report
                        policy.observe(report, time.monotonic(), datetime.now(timezone.utc))
            except socket.timeout:
                pass
            except (OSError, ValueError):
                if connection is not None:
                    connection.close()
                connection = None
    finally:
        if connection is not None:
            connection.close()
        client.disconnect()
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

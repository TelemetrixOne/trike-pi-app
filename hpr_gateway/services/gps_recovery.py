"""Bounded GPS-only recovery, independent of MQTT connectivity and GPS publisher."""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
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
    last_healthy_tpv: float | None = None
    nofix_attempted: bool = False
    attempts: list[float] = field(default_factory=list)
    attempt_limit: int = 3
    attempt_window: float = 600
    max_cooldown: float = 300
    consecutive_attempts: int = 0

    def observe(self, report: dict, now: float, wall: datetime) -> None:
        if report.get('class') == 'TPV' and report.get('mode') == 1:
            self.last_healthy_tpv = now
        if report.get("class") == "TPV" and valid_position(report, wall, 5):
            self.last_healthy_tpv = now
            # Repeated old TPVs cannot extend the last-fix clock.
            age = max(0.0, (wall - parse_gps_time(report["time"])).total_seconds())
            observed_fix = now - age
            self.last_fix = max(self.last_fix if self.last_fix is not None else observed_fix, observed_fix)
            if self.waiting_until is not None and observed_fix <= self.last_attempt:
                return
            self.waiting_until = None
            self.state = "healthy"
            self.nofix_attempted = False
            self.consecutive_attempts = 0

    def receiver_active(self, now):
        return self.last_healthy_tpv is not None and 0 <= now - self.last_healthy_tpv <= LOSS_SECONDS

    def cooldown(self):
        return min(self.max_cooldown, COOLDOWN_SECONDS * 2 ** min(4, max(0, self.consecutive_attempts - 1)))

    def tick(self, now: float) -> bool:
        if self.waiting_until is not None:
            if now >= self.waiting_until:
                self.waiting_until = None
                self.state = 'no_fix' if self.receiver_active(now) else 'failed'
            return False
        lost = (now - self.last_fix >= LOSS_SECONDS if self.last_fix is not None
                else now - self.started >= self.startup_grace)
        if not lost:
            return False
        # Fresh no-fix TPVs prove a live receiver. Do not reset cold acquisition
        # indefinitely. Keep one quick recovery attempt after a real fix loss.
        if self.receiver_active(now) and (self.last_fix is None or self.nofix_attempted):
            self.state = 'no_fix'
            return False
        self.attempts = [stamp for stamp in self.attempts if 0 <= now - stamp < self.attempt_window]
        if len(self.attempts) >= self.attempt_limit:
            self.state = 'budget_exhausted'
            return False
        if self.last_attempt is not None and now - self.last_attempt < self.cooldown():
            if self.state == "healthy":
                self.state = "cooldown"
            return False
        self.last_attempt = now
        self.attempts.append(now)
        self.consecutive_attempts += 1
        self.nofix_attempted = True
        self.waiting_until = now + REACQUIRE_SECONDS
        self.state = "recovering"
        return True


def load_last_attempt(path: Path, monotonic: float, wall: float) -> float | None:
    try:
        stamp = float(json.loads(path.read_text())["attempted_at_unix"])
        if not math.isfinite(stamp):
            raise ValueError('invalid recovery clock')
        # Backwards wall-clock steps must not bypass the cooldown.
        return monotonic - min(COOLDOWN_SECONDS, max(0.0, wall - stamp))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        LOG.warning("Invalid recovery timestamp; applying full cooldown")
        return monotonic


def save_attempt(path: Path, wall: float, policy=None) -> None:
    pending = path.with_suffix(".tmp")
    value = {'attempted_at_unix': wall}
    if policy is not None:
        value.update(attempts_unix=[wall - (policy.last_attempt - stamp) for stamp in policy.attempts],
                     nofix_attempted=policy.nofix_attempted, consecutive_attempts=policy.consecutive_attempts)
    with pending.open('w') as handle:
        json.dump(value, handle); handle.flush(); os.fsync(handle.fileno())
    pending.replace(path)


def restore_budget(path, policy, wall):
    policy.last_attempt = load_last_attempt(path, policy.started, wall)
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError('invalid recovery record')
        stamps = record.get('attempts_unix', [record['attempted_at_unix']])
        if not isinstance(stamps, list) or len(stamps) > policy.attempt_limit:
            raise ValueError('invalid attempt history')
        if any(type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 for stamp in stamps):
            raise ValueError('invalid attempt timestamp')
        policy.attempts = [policy.started - max(0, wall - float(stamp)) for stamp in stamps
                           if math.isfinite(float(stamp)) and wall - float(stamp) < policy.attempt_window]
        policy.consecutive_attempts = min(policy.attempt_limit, max(0, int(record.get('consecutive_attempts', 1))))
        policy.nofix_attempted = record.get('nofix_attempted', True) is True
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        policy.attempts = [policy.started] * policy.attempt_limit
        policy.state = 'budget_exhausted'
        LOG.error('GPS recovery budget unreadable; withholding resets pending bounded window')


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
    restore_budget(STATE_FILE, policy, time.time())
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
                    save_attempt(STATE_FILE, time.time(), policy)
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
                elif policy.state == 'no_fix' and previous_state != 'no_fix':
                    LOG.info('GPS receiver active without position fix; acquisition left undisturbed')
                client.publish(topic, json.dumps({"state": policy.state,
                               "last_fix_age_seconds": None if policy.last_fix is None else now - policy.last_fix,
                               "cooldown_remaining_seconds": 0 if policy.last_attempt is None else
                               max(0, policy.cooldown() - (now - policy.last_attempt)),
                               "attempts_in_window": len(policy.attempts), "attempt_limit": policy.attempt_limit,
                               "attempt_window_seconds": policy.attempt_window,
                               "receiver_active": policy.receiver_active(now),
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

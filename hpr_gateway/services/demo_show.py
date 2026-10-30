"""Explicit, broker-triggered trike demo. Never plays automatically at boot."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import logging
import queue
import signal
import sqlite3
import time
import uuid

from ..config import config_arg_parser, load_config
from ..mqtt import make_client
from ..demo_protocol import DemoJournal, envelope, request_identity
from ..drs_protocol import DRSClock as SynchronizedClock


LOG = logging.getLogger(__name__)

# Seconds from the accepted request. Each cue is an existing GPIO-control
# command; the demo never drives a pin directly. Leave time for restoration
# before the nominal 10-second finish. Do not compress actuator movement into
# rapid chatter: DRS direction changes remain at least two seconds apart.
SHOW_DURATION_SECONDS = 10.0
SHOW_END_SECONDS = 9.2
SHOW_CUES = (
    # Ignition chase: auxiliary, then main, then DRS reveal.
    (1.00, "aux_lights", "ON"), (1.25, "aux_lights", "OFF"),
    (1.35, "drs", "OPEN"),
    (1.95, "headlight", "ON"), (1.95, "horn", "ON"),
    (2.18, "horn", "OFF"), (2.35, "headlight", "OFF"),
    # Double hit, then a syncopated response with the auxiliary lights.
    (2.55, "aux_lights", "ON"), (2.55, "horn", "ON"),
    (2.78, "horn", "OFF"), (2.90, "aux_lights", "OFF"),
    (3.05, "headlight", "ON"), (3.05, "aux_lights", "ON"),
    (3.15, "horn", "ON"), (3.38, "horn", "OFF"),
    (3.55, "headlight", "OFF"), (3.60, "aux_lights", "OFF"),
    (3.70, "drs", "CLOSED"),
    # Alternating call-and-response, then both lights build together.
    (4.45, "headlight", "ON"), (4.45, "horn", "ON"),
    (4.67, "horn", "OFF"), (4.90, "headlight", "OFF"),
    (5.10, "aux_lights", "ON"), (5.10, "horn", "ON"),
    (5.33, "horn", "OFF"), (5.58, "aux_lights", "OFF"),
    (5.75, "drs", "OPEN"),
    (6.30, "headlight", "ON"), (6.30, "aux_lights", "ON"),
    (6.45, "horn", "ON"), (6.70, "horn", "OFF"),
    (6.95, "headlight", "OFF"), (7.10, "aux_lights", "OFF"),
    # Finale: main-light accent, full-light reveal, DRS close, final horn hit.
    (7.40, "headlight", "ON"), (7.40, "horn", "ON"),
    (7.62, "horn", "OFF"), (7.85, "drs", "CLOSED"),
    (8.45, "aux_lights", "ON"),
    (8.50, "horn", "ON"), (8.80, "horn", "OFF"),
    (8.85, "aux_lights", "OFF"), (8.90, "headlight", "OFF"),
)


def eligible(states: dict[str, str]) -> bool:
    """We need complete state to restore it; only an active horn is excluded."""
    required = (
        "horn/state", "headlight/state", "headlight/mode", "headlight/requested_state",
        "aux_lights/state", "aux_lights/requested_state",
        "drs/state", "drs/mode", "input/drs_switch/state", "input/headlamp/state",
    )
    if any(key not in states for key in required) or states["horn/state"] != "OFF":
        return False
    return (
        states["headlight/mode"] in ("AUTO", "ON", "OFF")
        and states["headlight/requested_state"] in ("ON", "OFF")
        and states["aux_lights/requested_state"] in ("ON", "OFF")
        and states["drs/mode"] in ("RIDER_CONTROL", "OPEN", "CLOSED")
        and states["input/drs_switch/state"] in ("ON", "OFF")
    )


def parse_request(raw: bytes, retained: bool) -> str:
    return envelope(raw, retained)['request_id']


class DemoShow:
    def __init__(self, config):
        self.config = config
        self.prefix = f"{config.topic_root.strip('/')}/{config.trike_id}"
        self.request_topic = f"{self.prefix}/control/demo/request"
        self.status_topic = f"{self.prefix}/control/demo/status"
        self.client = make_client(config, f"hpr-{config.trike_id}-demo-show")
        self.client.max_queued_messages_set(4)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.states: dict[str, str] = {}
        self.requests = queue.Queue(maxsize=1)
        self.journal = DemoJournal(config.get('services.demo_show.replay_db',
            os.path.join(os.getenv('STATE_DIRECTORY', '/var/lib/hpr-demo-show'), 'replay.sqlite3')))
        self.clock = SynchronizedClock()
        self.stopping = False
        self.running = False

    def status(self, state: str, request_id: str | None = None, reason: str | None = None):
        payload = {"state": state, "trike_id": self.config.trike_id,
                   "at": datetime.now(timezone.utc).isoformat()}
        if request_id:
            payload["request_id"] = request_id
        if reason:
            payload["reason"] = reason
        self.client.publish(self.status_topic, json.dumps(payload), qos=1, retain=True)

    def on_connect(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            return
        client.subscribe(self.request_topic, qos=1)
        client.subscribe(f"{self.prefix}/control/+/state", qos=1)
        client.subscribe(f"{self.prefix}/control/+/mode", qos=1)
        client.subscribe(f"{self.prefix}/control/+/requested_state", qos=1)
        client.subscribe(f"{self.prefix}/input/+/state", qos=1)
        self.status("ready")

    def on_message(self, _client, _userdata, message):
        if message.topic == self.request_topic:
            try:
                request = envelope(message.payload, bool(message.retain), self.clock.ready)
                request_id = request['request_id']
                if not self.journal.claim(request_id):
                    self.status('rejected', request_id, 'duplicate request identity')
                    return
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('rejected', request_identity(message.payload), str(exc))
                return
            if self.running:
                self.status("rejected", request_id, "demo already running")
                return
            try:
                self.requests.put_nowait(request)
            except queue.Full:
                self.status("rejected", request_id, "demo already queued or running")
            return
        if message.topic.startswith(self.prefix + "/"):
            key = message.topic[len(self.prefix) + 1:]
            if key.startswith("control/"):
                key = key[len("control/"):]
            self.states[key] = message.payload.decode(errors="replace").strip().upper()

    def send(self, name: str, value: str):
        result = self.sender.publish(f"{self.prefix}/control/{name}/set", value, qos=1, retain=False)
        result.wait_for_publish(timeout=1)
        if not result.is_published():
            raise RuntimeError(f"{name} command was not published")

    def wait_state(self, key: str, expected: str, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.states.get(key) == expected:
                return
            time.sleep(0.05)
        raise RuntimeError(f"{key} did not acknowledge {expected}")

    @staticmethod
    def wait_until(start: float, seconds: float):
        remaining = start + seconds - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def cue(self, name: str, value: str):
        self.send(name, value)
        key, expected = {
            "aux_lights": ("aux_lights/state", value),
            "headlight": ("headlight/state", value),
            "horn": ("horn/state", value),
            "drs": ("drs/state", "CLOSE" if value == "CLOSED" else "OPEN"),
        }[name]
        self.wait_state(key, expected)

    def play(self, request_id: str, expires_at=None):
        # Wait briefly for GPIO's retained state so we can restore it later.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not eligible(self.states):
            time.sleep(0.05)
        if not eligible(self.states):
            self.status("rejected", request_id, "complete GPIO state required; horn must be idle")
            return
        if expires_at is not None and (not self.clock.ready or time.time() >= expires_at):
            self.status('rejected', request_id, 'request expired while waiting for controller state')
            return
        original_mode = self.states["headlight/mode"]
        original_headlight_request = self.states["headlight/requested_state"]
        original_aux = self.states["aux_lights/requested_state"]
        original_drs = self.states["drs/mode"]
        self.status("running", request_id)
        started = time.monotonic()
        failure = None
        try:
            # Blackout before the first pass. Mode alone does not prove the
            # headlight output changed; require its effective and sensed states.
            self.send("aux_lights", "OFF")
            self.wait_state("aux_lights/requested_state", "OFF")
            self.wait_state("aux_lights/state", "OFF")
            self.send("headlight", "OFF")
            self.wait_state("headlight/mode", "OFF")
            self.wait_state("headlight/state", "OFF")
            self.wait_state("input/headlamp/state", "OFF")
            for when, name, value in SHOW_CUES:
                self.wait_until(started, when)
                self.cue(name, value)
            self.wait_until(started, SHOW_END_SECONDS)
        except Exception as exc:
            LOG.exception("Demo interrupted")
            failure = str(exc)
        finally:
            # Remote horn has its own pulse expiry. Restore original mode and
            # requested states even if the rider switch changed during play.
            if original_mode == "AUTO":
                try:
                    self.send("headlight", original_headlight_request)
                    deadline = time.monotonic() + 2
                    while self.states.get("headlight/requested_state") != original_headlight_request and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if self.states.get("headlight/requested_state") != original_headlight_request:
                        raise RuntimeError("headlight requested state not restored")
                except Exception:
                    LOG.exception("Unable to restore headlight requested state")
                    failure = "restoration of headlight requested state failed"
            for name, value, key in (
                ("horn", "OFF", "horn/state"),
                ("aux_lights", original_aux, "aux_lights/requested_state"),
                ("headlight", original_mode, "headlight/mode"),
                ("drs", original_drs, "drs/mode"),
            ):
                try:
                    self.send(name, value)
                    deadline = time.monotonic() + 2
                    while self.states.get(key) != value and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if self.states.get(key) != value:
                        raise RuntimeError(f"{name} restoration not acknowledged")
                except Exception:
                    LOG.exception("Unable to restore %s after demo", name)
                    failure = f"restoration of {name} failed"
            if failure is None:
                self.wait_until(started, SHOW_DURATION_SECONDS)
            self.status("failed" if failure else "completed", request_id, failure)

    def run(self) -> int:
        self.clock.start()
        self.client.connect(self.config.mqtt.host, self.config.mqtt.port, keepalive=30)
        self.client.loop_start()
        try:
            while not self.stopping:
                try:
                    request = self.requests.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.running = True
                self.sender = make_client(self.config, f'hpr-{self.config.trike_id}-demo-send-{uuid.uuid4().hex[:8]}',
                                          reconnect_on_failure=False)
                self.sender.max_queued_messages_set(1)
                try:
                    if not self.clock.ready or time.time() >= request['expires_at']:
                        self.status('rejected', request['request_id'], 'queued request expired')
                        continue
                    self.sender.connect(self.config.mqtt.host, self.config.mqtt.port, keepalive=30)
                    self.sender.loop_start()
                    self.play(request['request_id'], request['expires_at'])
                except Exception as exc:
                    self.status('failed', request['request_id'], type(exc).__name__)
                finally:
                    self.sender.disconnect(); self.sender.loop_stop()
                    self.running = False
        finally:
            self.clock.stop()
            self.client.disconnect()
            self.client.loop_stop()
        return 0


def main() -> int:
    args = config_arg_parser("Run the operator-triggered HPR demo listener").parse_args()
    config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    show = DemoShow(config)
    signal.signal(signal.SIGTERM, lambda *_: setattr(show, "stopping", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(show, "stopping", True))
    return show.run()


if __name__ == "__main__":
    raise SystemExit(main())

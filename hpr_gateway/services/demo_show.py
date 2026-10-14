"""Explicit, broker-triggered trike demo. Never plays automatically at boot."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import queue
import signal
import time
import uuid

from ..config import config_arg_parser, load_config
from ..mqtt import make_client


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
    (1.50, "headlight", "ON"), (1.85, "headlight", "OFF"),
    (2.00, "drs", "OPEN"),
    # Double hit, then a syncopated response with the auxiliary lights.
    (2.35, "headlight", "ON"), (2.35, "aux_lights", "ON"),
    (2.35, "horn", "ON"), (2.58, "horn", "OFF"),
    (2.80, "horn", "ON"), (3.03, "horn", "OFF"),
    (3.20, "headlight", "OFF"), (3.23, "aux_lights", "OFF"),
    (3.50, "aux_lights", "ON"), (3.50, "horn", "ON"),
    (3.72, "horn", "OFF"), (3.75, "aux_lights", "OFF"),
    (4.00, "drs", "CLOSED"),
    # Alternating call-and-response, then both lights build together.
    (4.45, "headlight", "ON"), (4.45, "horn", "ON"),
    (4.67, "horn", "OFF"), (4.90, "headlight", "OFF"),
    (5.10, "aux_lights", "ON"), (5.10, "horn", "ON"),
    (5.33, "horn", "OFF"), (5.58, "aux_lights", "OFF"),
    (5.85, "headlight", "ON"), (5.85, "aux_lights", "ON"),
    (6.10, "drs", "OPEN"),
    (6.50, "horn", "ON"), (6.72, "horn", "OFF"),
    (6.95, "headlight", "OFF"), (7.10, "aux_lights", "OFF"),
    # Finale: main-light accent, full-light reveal, DRS close, final horn hit.
    (7.40, "headlight", "ON"), (7.40, "horn", "ON"),
    (7.62, "horn", "OFF"), (7.85, "aux_lights", "ON"),
    (8.15, "drs", "CLOSED"),
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
    if retained:
        raise ValueError("retained requests are not accepted")
    try:
        message = json.loads(raw)
        if message.get("action") != "START" or set(message) != {"action", "request_id"}:
            raise ValueError("expected action START and request_id")
        request_id = str(uuid.UUID(message["request_id"]))
    except (TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid demo request") from exc
    return request_id


class DemoShow:
    def __init__(self, config):
        self.config = config
        self.prefix = f"{config.topic_root.strip('/')}/{config.trike_id}"
        self.request_topic = f"{self.prefix}/control/demo/request"
        self.status_topic = f"{self.prefix}/control/demo/status"
        self.client = make_client(config, f"hpr-{config.trike_id}-demo-show")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.states: dict[str, str] = {}
        self.requests: queue.Queue[str] = queue.Queue(maxsize=1)
        self.seen: set[str] = set()
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
                request_id = parse_request(message.payload, bool(message.retain))
            except ValueError as exc:
                self.status("rejected", reason=str(exc))
                return
            if request_id in self.seen:
                return
            self.seen.add(request_id)
            if len(self.seen) > 128:
                self.seen = {request_id}
            if self.running:
                self.status("rejected", request_id, "demo already running")
                return
            try:
                self.requests.put_nowait(request_id)
            except queue.Full:
                self.status("rejected", request_id, "demo already queued or running")
            return
        if message.topic.startswith(self.prefix + "/"):
            key = message.topic[len(self.prefix) + 1:]
            if key.startswith("control/"):
                key = key[len("control/"):]
            self.states[key] = message.payload.decode(errors="replace").strip().upper()

    def send(self, name: str, value: str):
        result = self.client.publish(f"{self.prefix}/control/{name}/set", value, qos=1, retain=False)
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

    def play(self, request_id: str):
        # Wait briefly for GPIO's retained state so we can restore it later.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not eligible(self.states):
            time.sleep(0.05)
        if not eligible(self.states):
            self.status("rejected", request_id, "complete GPIO state required; horn must be idle")
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
        self.client.connect(self.config.mqtt.host, self.config.mqtt.port, keepalive=30)
        self.client.loop_start()
        try:
            while not self.stopping:
                try:
                    request_id = self.requests.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.running = True
                try:
                    self.play(request_id)
                finally:
                    self.running = False
        finally:
            self.client.loop_stop()
            self.client.disconnect()
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

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
MAX_GPS_AGE = 3.0
MAX_SPEED_KMH = 1.0


def eligible(states: dict[str, str]) -> bool:
    """We need complete state to restore it; only an active horn is excluded."""
    required = (
        "horn/state", "headlight/state", "headlight/mode", "headlight/requested_state",
        "aux_lights/state", "aux_lights/requested_state",
        "drs/state", "drs/mode", "input/drs_switch/state",
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


def moving_nav(payload: dict, received_at: float, now: float) -> bool:
    """Fresh valid GPS motion vetoes a supervised pit demo."""
    try:
        speed = float(payload["speed_kmh"])
        fix_mode = int(payload["fix_mode"])
        fix_age = float(payload["fix_age_ms"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        payload.get("location_valid") is True
        and fix_mode >= 2
        and 0 <= fix_age <= 2000
        and 0 <= now - received_at <= MAX_GPS_AGE
        and speed > MAX_SPEED_KMH
    )


def parse_request(raw: bytes, retained: bool) -> str:
    if retained:
        raise ValueError("retained requests are not accepted")
    try:
        message = json.loads(raw)
        if message.get("action") != "START" or set(message) != {"action", "request_id", "stationary_confirmed"}:
            raise ValueError("expected action START, request_id, and stationary_confirmed")
        if message["stationary_confirmed"] is not True:
            raise ValueError("operator must confirm trike is stationary")
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
        self.nav_topic = config.resolve_topic(config.get("gps.topic"), "nav")
        self.client = make_client(config, f"hpr-{config.trike_id}-demo-show")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.states: dict[str, str] = {}
        self.nav: dict = {}
        self.nav_at = 0.0
        self.initial_switch = ""
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
        client.subscribe(self.nav_topic, qos=0)
        self.status("ready")

    def on_message(self, _client, _userdata, message):
        if message.topic == self.nav_topic:
            try:
                self.nav = json.loads(message.payload)
                self.nav_at = time.monotonic()
            except (TypeError, ValueError):
                self.nav = {}
                self.nav_at = 0.0
            return
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

    def safe_to_continue(self) -> bool:
        return (
            not moving_nav(self.nav, self.nav_at, time.monotonic())
            and self.states.get("input/drs_switch/state") == self.initial_switch
        )

    def wait_safe(self, duration: float):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not self.safe_to_continue():
                raise RuntimeError("GPS motion or DRS switch change")
            time.sleep(max(0, min(0.05, deadline - time.monotonic())))

    def wait_state(self, key: str, expected: str, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.states.get(key) == expected:
                return
            self.wait_safe(0.05)
        raise RuntimeError(f"{key} did not acknowledge {expected}")

    def play(self, request_id: str):
        # Wait briefly for GPIO's retained state; GPS may not have an indoor fix.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not eligible(self.states):
            time.sleep(0.05)
        if not eligible(self.states):
            self.status("rejected", request_id, "complete GPIO state required; horn must be idle")
            return
        if moving_nav(self.nav, self.nav_at, time.monotonic()):
            self.status("rejected", request_id, "GPS reports motion")
            return

        original_mode = self.states["headlight/mode"]
        original_headlight_request = self.states["headlight/requested_state"]
        original_aux = self.states["aux_lights/requested_state"]
        original_drs = self.states["drs/mode"]
        self.initial_switch = self.states["input/drs_switch/state"]
        self.status("running", request_id)
        failure = None
        try:
            for _ in range(2):
                if not self.safe_to_continue():
                    raise RuntimeError("motion or DRS switch change")
                self.send("aux_lights", "ON")
                self.wait_state("aux_lights/state", "ON")
                self.wait_safe(0.18)
                self.send("aux_lights", "OFF")
                self.wait_state("aux_lights/state", "OFF")
                self.send("headlight", "ON")
                self.wait_state("headlight/state", "ON")
                self.wait_safe(0.18)
                self.send("headlight", "OFF")
                self.wait_state("headlight/state", "OFF")
            for duration in (0.16, 0.16, 0.35):
                if not self.safe_to_continue():
                    raise RuntimeError("motion or DRS switch change")
                self.send("horn", "ON")
                self.wait_state("horn/state", "ON")
                self.wait_safe(duration)
                self.send("horn", "OFF")
                self.wait_state("horn/state", "OFF")
                self.wait_safe(0.18)
            if not self.safe_to_continue():
                raise RuntimeError("motion or DRS switch change")
            self.send("drs", "OPEN")
            self.wait_state("drs/state", "OPEN")
            self.wait_safe(1.0)
            if not self.safe_to_continue():
                raise RuntimeError("motion or DRS switch change")
            self.send("drs", "CLOSED")
            self.wait_state("drs/state", "CLOSE")
            self.wait_safe(0.7)
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

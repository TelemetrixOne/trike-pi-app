"""Single, bounded boot flourish through the existing GPIO MQTT controller.

No direct GPIO access, no DRS motion, and no dependency on the video workers.
"""

from __future__ import annotations

import logging
from pathlib import Path
import time

from ..config import config_arg_parser, load_config
from ..mqtt import make_client


LOG = logging.getLogger(__name__)
MARKER = Path("/run/hpr-startup/show.done")
MAX_BOOT_AGE_SECONDS = 90


def eligible(states: dict[str, str], *, headlight: bool, aux: bool, horn: bool) -> bool:
    """Never interrupt a light or horn already in use."""
    required = ["horn/state"] if horn else []
    required += ["headlight/state", "headlight/mode", "input/headlamp/state"] if headlight else []
    required += ["aux_lights/state", "input/aux_lights/state"] if aux else []
    if not all(key in states for key in required):
        return False
    if any(states[key] != "OFF" for key in required if key.endswith("/state")):
        return False
    return not headlight or states["headlight/mode"] in ("AUTO", "OFF")


def main() -> int:
    args = config_arg_parser("Play the bounded HPR boot flourish").parse_args()
    config = load_config(args.config)
    logging.basicConfig(level=config.get("logging.level", "INFO"))
    if MARKER.exists():
        return 0
    # A manual service restart long after boot must never sound the horn.
    try:
        if float(Path("/proc/uptime").read_text().split()[0]) > MAX_BOOT_AGE_SECONDS:
            LOG.info("Skipping startup show outside boot window")
            return 0
    except (OSError, ValueError):
        return 0
    MARKER.touch()
    circuits = config.get("gpio.circuits", {}) or {}
    headlight = bool((circuits.get("headlight") or {}).get("enabled"))
    aux = bool((circuits.get("aux_lights") or {}).get("enabled"))
    horn = bool((circuits.get("horn") or {}).get("enabled"))
    if not config.enabled("gpio_control") or not (headlight or aux or horn):
        return 0

    states: dict[str, str] = {}
    connected = False
    client = make_client(config, f"hpr-{config.trike_id}-startup-show")
    root = config.topic_root.strip("/")
    prefix = f"{root}/{config.trike_id}"

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        nonlocal connected
        connected = reason_code == 0
        if connected:
            _client.subscribe(f"{prefix}/control/+/state", qos=1)
            _client.subscribe(f"{prefix}/control/headlight/mode", qos=1)
            _client.subscribe(f"{prefix}/input/+/state", qos=1)

    def on_message(_client, _userdata, message):
        if not message.topic.startswith(prefix + "/"):
            return
        key = message.topic[len(prefix) + 1:]
        if key.startswith("control/"):
            key = key[len("control/"):]
        states[key] = message.payload.decode(errors="replace").strip().upper()

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(config.mqtt.host, config.mqtt.port, keepalive=15)
        client.loop_start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (connected and eligible(states, headlight=headlight, aux=aux, horn=horn)):
            time.sleep(0.05)
        if not connected or not eligible(states, headlight=headlight, aux=aux, horn=horn):
            LOG.info("Skipping startup show: GPIO state unavailable or outputs in use")
            return 0

        original_headlight_mode = states.get("headlight/mode", "OFF")

        def send(name: str, value: str):
            result = client.publish(f"{prefix}/control/{name}/set", value, qos=1, retain=False)
            result.wait_for_publish(timeout=1)

        try:
            # Brief light choreography and a fixed-pitch rhythmic horn signature.
            for _ in range(2):
                if aux: send("aux_lights", "ON")
                time.sleep(0.18)
                if aux: send("aux_lights", "OFF")
                if headlight: send("headlight", "ON")
                time.sleep(0.18)
                if headlight: send("headlight", "OFF")
            if horn:
                for duration in (0.16, 0.16, 0.35):
                    send("horn", "ON")
                    time.sleep(duration)
                    send("horn", "OFF")
                    time.sleep(0.18)
        finally:
            if horn: send("horn", "OFF")
            if aux: send("aux_lights", "OFF")
            if headlight: send("headlight", original_headlight_mode)
        LOG.info("Startup show completed")
    except Exception:
        LOG.exception("Startup show failed; GPIO and video services remain independent")
        return 0
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

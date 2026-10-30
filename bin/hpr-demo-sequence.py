#!/usr/bin/env python3
"""Start the existing on-demand trike demo from the Pi console.

This sends one fresh, non-retained MQTT request. The demo listener remains the
only component that drives the lights, horn and DRS and restores their modes.
"""

import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpr_gateway.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from hpr_gateway.mqtt import make_client  # noqa: E402


TERMINAL_STATES = {"completed", "failed", "rejected"}


def run_demo(config, timeout=45.0, client=None, request_id=None, output=print):
    """Return 0 only when the listener confirms completion."""
    request_id = request_id or str(uuid.uuid4())
    prefix = f"{config.topic_root.strip('/')}/{config.trike_id}"
    request_topic = f"{prefix}/control/demo/request"
    status_topic = f"{prefix}/control/demo/status"
    client = client or make_client(config, f"hpr-{config.trike_id}-demo-cli-{uuid.uuid4().hex[:8]}",
                                   reconnect_on_failure=False)
    subscribed = threading.Event()
    messages = queue.Queue()

    def on_connect(mqtt_client, _userdata, _flags, reason_code, _properties=None):
        if reason_code != 0:
            messages.put(("connection_error", str(reason_code)))
            subscribed.set()
            return
        result, _mid = mqtt_client.subscribe(status_topic, qos=1)
        if result != 0:
            messages.put(("subscription_error", str(result)))
            subscribed.set()

    def on_subscribe(_mqtt_client, _userdata, _mid, *_args):
        subscribed.set()

    def on_message(_mqtt_client, _userdata, message):
        if message.topic != status_topic or getattr(message, 'retain', False):
            return
        try:
            status = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        if status.get("request_id") == request_id:
            messages.put(("status", status))

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.connect(config.mqtt.host, config.mqtt.port, keepalive=30)
    client.loop_start()
    try:
        if not subscribed.wait(timeout=min(timeout, 10)):
            output("No MQTT status subscription; demo not requested.")
            return 2
        try:
            kind, detail = messages.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind != "status":
                output(f"MQTT {kind}: {detail}; demo not requested.")
                return 2

        issued = time.time()
        payload = json.dumps({'schema_version': 1, 'action': 'START', 'request_id': request_id,
                              'issued_at': issued, 'expires_at': issued + 5})
        published = client.publish(request_topic, payload, qos=1, retain=False)
        published.wait_for_publish(timeout=5)
        if not published.is_published():
            output("Demo request was not acknowledged by the broker.")
            return 2
        output(f"Demo requested for {config.trike_id}; request ID: {request_id}")

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                output("Timed out waiting for demo completion. Check hpr-demo-show.service.")
                return 2
            try:
                kind, detail = messages.get(timeout=remaining)
            except queue.Empty:
                output("Timed out waiting for demo completion. Check hpr-demo-show.service.")
                return 2
            if kind != "status":
                output(f"MQTT {kind}: {detail}")
                return 2
            state = detail.get("state", "unknown")
            reason = detail.get("reason")
            output(f"Demo {state}" + (f": {reason}" if reason else ""))
            if state in TERMINAL_STATES:
                return 0 if state == "completed" else 1
    finally:
        client.disconnect()
        client.loop_stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Pi's on-demand lights, horn and DRS demo")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="synced Pi configuration")
    parser.add_argument("--timeout", type=float, default=45.0, help="seconds to await completion")
    parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.yes:
        try:
            answer = input("Run the 10-second lights, horn and DRS demo? Type YES: ")
        except EOFError:
            answer = ""
        if answer != "YES":
            print("Demo cancelled; no command sent.")
            return 1
    try:
        return run_demo(load_config(args.config), timeout=args.timeout)
    except (OSError, ValueError) as exc:
        print(f"Demo request could not be sent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

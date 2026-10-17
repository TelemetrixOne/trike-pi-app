#!/usr/bin/env python3
"""Observe real retained/live GPS health; never publish or alter the receiver."""
import argparse
import json
import time
from datetime import datetime, timezone

from hpr_gateway.config import load_config
from hpr_gateway.mqtt import make_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/hpr/hpr.yaml")
    parser.add_argument("--require-fix", action="store_true")
    parser.add_argument("--require-recovery", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    status_topic = cfg.resolve_topic(cfg.get("gps.status_topic"), "nav", "status")
    detail_topic = cfg.resolve_topic(cfg.get("health.base_topic"), "pi") + "/gps_detail"
    recovery_topic = cfg.resolve_topic(cfg.get("gps.recovery_topic"), "nav", "recovery")
    seen = {}
    counts = {}
    client = make_client(cfg, "gps-validation-" + str(time.time_ns()))

    def on_message(_client, _userdata, msg):
        try:
            value = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(value, dict):
            seen[msg.topic] = value
            if not msg.retain:
                counts[msg.topic] = counts.get(msg.topic, 0) + 1

    client.on_connect = lambda c, *_: c.subscribe([(status_topic, 1), (detail_topic, 1), (recovery_topic, 1)])
    client.on_message = on_message
    client.connect(cfg.mqtt.host, cfg.mqtt.port, 15)
    client.loop_start()
    try:
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            status = seen.get(status_topic, {})
            detail = seen.get(detail_topic, {})
            recovery = seen.get(recovery_topic, {})
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(status["observed_at"])).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = 999
            if (counts.get(status_topic, 0) >= 2 and counts.get(detail_topic, 0) >= 1
                    and 0 <= age <= 10 and status.get("state") == "online"
                    and detail.get("reason") in {"position_fix", "no_position_fix"}
                    and (not args.require_recovery or counts.get(recovery_topic, 0) >= 2
                         and recovery.get("state") == "healthy"
                         and (detail.get("recovery") or {}).get("state") == "healthy")
                    and (not args.require_fix or status.get("fix_valid") is True
                         and detail.get("fix_valid") is True)):
                print(json.dumps({"result": "PASS", "live_status_messages": counts[status_topic],
                                  "status": status, "health": detail}))
                return 0
            time.sleep(0.2)
        print(json.dumps({"result": "FAIL", "messages": counts, "last": seen}))
        return 1
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import subprocess
import time

from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import connect_client


def read_throttled() -> tuple[str, str]:
    try:
        output = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "Unknown", "error"
    raw = output.split("=", 1)[-1] if "=" in output else output
    try:
        value = int(raw, 16)
    except ValueError:
        return "Unknown", raw
    return ("Under" if value & (0x1 | 0x10000) else "Good"), raw


def main() -> int:
    parser = config_arg_parser("Publish Raspberry Pi power health")
    args = parser.parse_args()
    config = load_config(args.config)
    service = config.get("services.pi_power_health", {})
    interval = float(service.get("interval_seconds", 10))
    power_topic = config.resolve_topic(service.get("power_topic"), "pi", "power_health")
    raw_topic = config.resolve_topic(service.get("raw_topic"), "pi", "throttled_raw")
    service_topic = config.resolve_topic(service.get("service_topic"), "health", "services", "pi_power")
    client = connect_client(config, f"hpr-{config.trike_id}-pi-power-health")
    client.publish(service_topic, payload="online", qos=1, retain=True)
    while True:
        health, raw = read_throttled()
        client.publish(power_topic, payload=health, qos=1, retain=True)
        client.publish(raw_topic, payload=raw, qos=1, retain=True)
        client.publish(service_topic, payload="online", qos=1, retain=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

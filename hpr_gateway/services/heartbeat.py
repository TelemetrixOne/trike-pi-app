from __future__ import annotations

import time
from datetime import datetime, timezone

from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import connect_client


def main() -> int:
    parser = config_arg_parser("Publish HPR gateway heartbeat")
    args = parser.parse_args()
    config = load_config(args.config)
    service = config.get("services.heartbeat", {})
    interval = float(service.get("interval_seconds", 5))
    topics = [config.resolve_topic(service.get("topic"), "pi", "heartbeat")]
    topics.extend(config.resolve_topic(topic) for topic in (service.get("legacy_topics", []) or []))
    client = connect_client(config, f"hpr-{config.trike_id}-heartbeat")
    while True:
        payload = datetime.now(timezone.utc).isoformat()
        for topic in topics:
            client.publish(topic, payload=payload, qos=1, retain=False)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

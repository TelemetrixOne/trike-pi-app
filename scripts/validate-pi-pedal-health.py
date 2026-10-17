#!/usr/bin/env python3
"""Read-only validation of live pedal data and consistent health reporting."""
import json
import time
from datetime import datetime, timezone

from hpr_gateway.config import load_config, enabled_items
from hpr_gateway.mqtt import make_client


def main():
    cfg = load_config('/etc/hpr/hpr.yaml')
    detail_topic = cfg.resolve_topic(cfg.get('health.base_topic'), 'pi') + '/power_cadence_detail'
    power_topics = [cfg.resolve_topic(d.get('topic_base'), 'pedals', name).rstrip('/') + '/power_w'
                    for name, d in enabled_items(cfg.get('power_cadence.devices', {}))]
    counts = {}
    latest = {}

    def on_message(_c, _u, msg):
        if not msg.retain:
            counts[msg.topic] = counts.get(msg.topic, 0) + 1
        if msg.topic == detail_topic:
            try:
                latest.update(json.loads(msg.payload))
            except (ValueError, UnicodeDecodeError):
                pass

    client = make_client(cfg, 'pedal-health-validation-' + str(time.time_ns()))
    client.on_connect = lambda c, *_: c.subscribe([(t, 1) for t in [detail_topic, *power_topics]])
    client.on_message = on_message
    client.connect(cfg.mqtt.host, cfg.mqtt.port, 15)
    client.loop_start()
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest['observed_at'])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = 999
            if (power_topics and all(counts.get(t, 0) >= 2 for t in power_topics)
                    and counts.get(detail_topic, 0) >= 1 and 0 <= age <= 10
                    and latest.get('device') == 'connected' and latest.get('data') == 'fresh'
                    and latest.get('overall') in {'Good', 'Disabled'}):
                print(json.dumps({'result': 'PASS', 'live_messages': counts, 'detail': latest}))
                return 0
            time.sleep(0.2)
        print(json.dumps({'result': 'FAIL', 'live_messages': counts, 'detail': latest}))
        return 1
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    raise SystemExit(main())

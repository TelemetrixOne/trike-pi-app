#!/usr/bin/env python3
"""Read-only validation of two fresh system-metrics MQTT samples and legacy topics."""
import argparse
from datetime import datetime, timezone
import json
import threading
import time

from hpr_gateway.config import load_config
from hpr_gateway.mqtt import make_client

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', default='/etc/hpr/hpr.yaml')
args = parser.parse_args()
config = load_config(args.config)
base = config.resolve_topic(config.get('health.base_topic'), 'pi')
topic = f'{base}/system_metrics'
samples, legacy, failures = [], set(), []
done = threading.Event()
client = make_client(config, f'hpr-{config.trike_id}-metrics-validation-{int(time.time())}')


def connected(c, _u, _f, _code, *_extra):
    c.subscribe(f'{base}/#', qos=1)


def received(_c, _u, message):
    if message.topic != topic:
        if message.topic.endswith('_health') and not message.retain:
            legacy.add(message.topic)
        return
    try:
        data = json.loads(message.payload)
        assert data['schema'] == 'hpr.pi_system_metrics.v1'
        assert data['trike_id'] == config.trike_id
        if data['observed_at'] is None and data['errors'] == ['not_sampled']:
            return
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data['observed_at'])).total_seconds()
        assert -5 <= age < 30, f'Stale sample: {age}'
        if data['cpu_usage_pct'] is None and data['errors'] == ['cpu_sample_unavailable']:
            return  # First CPU sample needs an interval; do not call it zero.
        assert not data['errors'], data['errors']
        assert 0 <= data['cpu_usage_pct'] <= 100
        assert 0 <= data['memory_used_pct'] <= 100
        assert 0 <= data['disk_used_pct'] <= 100
        assert -40 <= data['cpu_temperature_c'] <= 150
        assert data['logs_complete'] and data['logs_window_seconds'] == 1800
        assert type(data['warnings_count']) is int and data['warnings_count'] >= 0
        assert type(data['errors_count']) is int and data['errors_count'] >= 0
        logs_age = (datetime.now(timezone.utc) - datetime.fromisoformat(data['logs_observed_at'])).total_seconds()
        assert -5 <= logs_age < 120, f'Stale logs: {logs_age}'
        if not samples or samples[-1]['observed_at'] != data['observed_at']:
            samples.append(data)
        if len(samples) >= 2 and legacy:
            done.set()
    except Exception as exc:
        failures.append(str(exc))
        done.set()


client.on_connect = connected
client.on_message = received
client.connect(config.mqtt.host, config.mqtt.port, 30)
client.loop_start()
try:
    assert done.wait(40), 'Timed out waiting for two fresh metrics and existing service-health messages'
    assert not failures, failures
    assert len(samples) >= 2 and legacy, 'Incomplete evidence'
    print(json.dumps(samples[-1], sort_keys=True))
    print(f'PASS: two fresh metrics samples, {len(legacy)} existing service-health topics still publishing')
finally:
    client.disconnect()
    client.loop_stop()

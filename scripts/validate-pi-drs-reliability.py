#!/usr/bin/env python3
"""Passive DRS-only status validation. Never publishes actuator commands."""
import json
import math
import subprocess
import time
import uuid

import paho.mqtt.client as mqtt
from hpr_gateway.config import load_config

c = load_config('/etc/hpr/hpr.yaml')
topic = f'{c.topic_root}/{c.trike_id}/control/drs/status'
samples, problems = [], []
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='drs-passive-validation-' + str(uuid.uuid4()))
client.username_pw_set(c.mqtt.username, c.mqtt.password)

def connected(cl, userdata, flags, reason, props):
    cl.subscribe(topic, qos=1)

def message(cl, userdata, msg):
    try:
        value = json.loads(msg.payload)
        assert value['schema_version'] == 1
        assert value['position_verified'] is False
        assert value['mode'] in ('OPEN', 'CLOSED', 'RIDER_CONTROL')
        assert value['target'] in ('OPEN', 'CLOSED')
        assert value['phase'] in ('idle', 'moving', 'holding', 'released', 'error')
        assert value['phase'] != 'error', value.get('error')
        assert value['clock_ready'] is True, 'DRS clock not ready'
        uuid.UUID(value['boot_id'])
        assert type(value['seq']) is int and value['seq'] > 0
        assert math.isfinite(value['published_at']) and abs(time.time() - value['published_at']) < 8
        if samples:
            assert value['boot_id'] == samples[-1]['boot_id'], 'DRS owner restarted'
            if value['seq'] == samples[-1]['seq']:
                return  # QoS redelivery is not a new heartbeat.
            assert value['seq'] > samples[-1]['seq'], 'Regressed sequence'
        samples.append(value)
    except (KeyError, ValueError, TypeError, AssertionError) as exc:
        problems.append(str(exc))

client.on_connect, client.on_message = connected, message
client.connect(c.mqtt.host, c.mqtt.port, 30)
client.loop_start()
deadline = time.monotonic() + 25
while time.monotonic() < deadline and len(samples) < 2:
    time.sleep(0.2)
client.disconnect(); client.loop_stop()
assert len(samples) >= 2, f'No two fresh DRS heartbeats: {problems[-3:]}'
assert subprocess.check_output(['systemctl', 'is-active', 'hpr-gpio-control.service'], text=True).strip() == 'active'
print('PASS DRS schema1: fresh increasing heartbeats, clock ready, owner stable, no physical-position claim')
print(json.dumps(samples[-1], sort_keys=True))

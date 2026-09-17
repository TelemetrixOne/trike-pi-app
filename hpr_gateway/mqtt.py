from __future__ import annotations

import json
from typing import Any

import paho.mqtt.client as mqtt

from .config import HprConfig


def make_client(config: HprConfig, client_id: str) -> mqtt.Client:
    try:
        client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        client = mqtt.Client(client_id=client_id)
    client.username_pw_set(config.mqtt.username, config.mqtt.password)
    return client


def connect_client(
    config: HprConfig,
    client_id: str,
    keepalive: int = 60,
    *,
    will_topic: str | None = None,
    will_payload: str | None = None,
    will_qos: int = 0,
    will_retain: bool = False,
) -> mqtt.Client:
    client = make_client(config, client_id)
    if will_topic:
        client.will_set(
            will_topic,
            payload=will_payload,
            qos=will_qos,
            retain=will_retain,
        )
    client.connect(config.mqtt.host, config.mqtt.port, keepalive=keepalive)
    client.loop_start()
    return client


def encode_payload(payload: Any) -> str:
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return str(payload)

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from bleak import BleakScanner

from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.config import config_arg_parser, enabled_items, load_config
from hpr_gateway.mqtt import connect_client, encode_payload

LOG = logging.getLogger("hpr_tpms")
STOP = asyncio.Event()


def normalize_mac(mac: str | None) -> str | None:
    return mac.strip().lower() if mac else None


def decode_ai8000(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < 12:
        return None
    pressure_raw = int.from_bytes(payload[7:9], "little", signed=False)
    temp_raw = int.from_bytes(payload[10:12], "little", signed=False)
    return {
        "psi": round((pressure_raw + 9.5) / 27.025, 2),
        "temperature_c": round(temp_raw / 100.0, 2),
        "pressure_raw": pressure_raw,
        "decoder": "tpms1_off07_u16_le_linear_fit",
    }


def decode_payload(profile: str, payload: bytes) -> dict[str, Any] | None:
    if profile in {"ai8000", "tpms1"}:
        return decode_ai8000(payload)
    raise ValueError(f"unsupported TPMS decode_profile: {profile}")


async def run(config_path: str) -> int:
    config = load_config(config_path)
    logging.basicConfig(level=getattr(logging, config.get("logging.level", "INFO").upper()))
    service = config.get("services.tpms", {})
    sensors = {}
    for name, sensor in enabled_items(config.get("tpms.sensors", {})):
        sensors[normalize_mac(sensor["mac"])] = {
            **sensor,
            "name": name,
            "samples": deque(maxlen=int(service.get("smooth_window", 5))),
            "last_seen": 0.0,
            "last_publish": 0.0,
            "last_psi": None,
            "online": False,
        }
    client = connect_client(config, f"hpr-{config.trike_id}-tpms")
    qos = int(service.get("publish_qos", 1))
    retain = bool(service.get("publish_retain", False))
    stale_seconds = float(service.get("stale_seconds", 300))
    min_delta = float(service.get("publish_min_delta_psi", 0.2))
    max_interval = float(service.get("publish_max_interval_seconds", 10))
    manufacturer_id = int(str(config.get("tpms.manufacturer_id", "0x0100")), 0)

    def publish(topic: str, payload: Any) -> None:
        client.publish(topic, payload=encode_payload(payload), qos=qos, retain=retain)

    def callback(device, adv) -> None:
        mac = normalize_mac(getattr(device, "address", None))
        if not mac or mac not in sensors:
            return
        sensor = sensors[mac]
        manufacturer_data = getattr(adv, "manufacturer_data", {}) or {}
        payload_data = manufacturer_data.get(manufacturer_id)
        if payload_data is None:
            return
        payload = bytes(payload_data)
        state = decode_payload(sensor["decode_profile"], payload)
        base_topic = config.resolve_topic(sensor.get("topic"), "tpms", sensor["name"]).rstrip("/")
        now = datetime.now(timezone.utc).isoformat()
        now_monotonic = time.monotonic()
        sensor["last_seen"] = now_monotonic
        sensor["online"] = True
        publish(f"{base_topic}/availability", "online")
        publish(f"{base_topic}/raw", {"ts": now, "mac": mac.upper(), "raw_hex": payload.hex()})
        if state:
            sensor["samples"].append(float(state["psi"]))
            smoothed_psi = sum(sensor["samples"]) / len(sensor["samples"])
            should_publish = (
                sensor["last_psi"] is None
                or abs(smoothed_psi - sensor["last_psi"]) >= min_delta
                or now_monotonic - sensor["last_publish"] >= max_interval
            )
            if should_publish:
                state["psi"] = round(smoothed_psi, 2)
                publish(base_topic, {**state, "ts": now, "mac": mac.upper(), "trike_id": config.trike_id})
                sensor["last_psi"] = smoothed_psi
                sensor["last_publish"] = now_monotonic
                LOG.info("published %s %s", base_topic, state)

    def stop(*_args) -> None:
        STOP.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    adapter = resolve_bluetooth_role(
        config, "telemetry", legacy_adapter_path="tpms.bluetooth_adapter"
    )
    scanner = BleakScanner(callback, adapter=adapter)
    await scanner.start()
    try:
        while not STOP.is_set():
            now_monotonic = time.monotonic()
            for sensor in sensors.values():
                if sensor["online"] and now_monotonic - sensor["last_seen"] > stale_seconds:
                    topic = config.resolve_topic(sensor.get("topic"), "tpms", sensor["name"])
                    client.publish(f"{topic.rstrip('/')}/availability", payload="offline", qos=qos, retain=retain)
                    sensor["online"] = False
            await asyncio.sleep(1)
    finally:
        await scanner.stop()
        for sensor in sensors.values():
            topic = config.resolve_topic(sensor.get("topic"), "tpms", sensor["name"])
            client.publish(f"{topic.rstrip('/')}/availability", payload="offline", qos=qos, retain=retain)
        client.loop_stop()
        client.disconnect()
    return 0


def main() -> int:
    parser = config_arg_parser("Publish HPR TPMS BLE telemetry")
    args = parser.parse_args()
    return asyncio.run(run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())

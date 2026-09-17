from __future__ import annotations

import asyncio
import json
import signal
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import connect_client

NUS_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
STOP = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_gear(data: bytes) -> int | None:
    if len(data) != 13 or data[0] != 0xFE:
        return None
    gear = data[8] ^ data[9]
    return gear if 1 <= gear <= 14 else None


async def run(config_path: str) -> int:
    config = load_config(config_path)
    cfg = config.get("derailleur", {}) or {}
    mac = str(cfg["mac"]).upper()
    device_name = str(cfg.get("device_name") or "EDS OX")
    adapter = resolve_bluetooth_role(
        config, "telemetry", legacy_adapter_path="derailleur.bluetooth_adapter"
    )
    scan_timeout = float(cfg.get("scan_timeout_seconds", 30))
    connect_timeout = float(cfg.get("connect_timeout_seconds", 20))
    stale_timeout = float(cfg.get("notification_stale_timeout_seconds", 30))
    reconnect_initial = float(cfg.get("reconnect_initial_seconds", 2))
    reconnect_max = float(cfg.get("reconnect_max_seconds", 15))
    notify_uuid = str(cfg.get("notify_uuid") or NUS_NOTIFY_UUID)

    mqttc = connect_client(config, f"hpr-{config.trike_id}-derailleur")
    base = config.resolve_topic(cfg.get("topic_base"), "derailleur").rstrip("/")
    topics = {name: f"{base}/{name}" for name in ("gear", "state", "last_seen", "raw", "status")}
    delay = reconnect_initial
    last_gear: int | None = None

    while not STOP:
        client: BleakClient | None = None
        notification_started = False
        real_data = False
        try:
            mqttc.publish(topics["status"], "scanning", qos=1, retain=True)
            device = await BleakScanner.find_device_by_address(
                mac, timeout=scan_timeout, adapter=adapter
            )
            if device is None:
                raise RuntimeError(f"derailleur {mac} not found on {adapter}")
            client = BleakClient(device, timeout=connect_timeout)
            await asyncio.wait_for(client.connect(), timeout=connect_timeout + 5)
            mqttc.publish(topics["status"], "online", qos=1, retain=True)
            last_notification = asyncio.get_running_loop().time()

            def on_notify(_sender, payload: bytearray) -> None:
                nonlocal last_gear, last_notification, real_data, delay
                raw = bytes(payload)
                last_notification = asyncio.get_running_loop().time()
                real_data = True
                delay = reconnect_initial
                now = utc_now()
                mqttc.publish(topics["last_seen"], now, qos=1, retain=True)
                mqttc.publish(topics["raw"], raw.hex(" "), qos=0, retain=False)
                gear = decode_gear(raw)
                if gear is None or gear == last_gear:
                    return
                last_gear = gear
                mqttc.publish(topics["gear"], str(gear), qos=1, retain=True)
                mqttc.publish(
                    topics["state"],
                    json.dumps(
                        {
                            "trike": config.trike_id,
                            "device": device_name,
                            "mac": mac,
                            "gear": gear,
                            "last_seen": now,
                            "raw": raw.hex(" "),
                        },
                        separators=(",", ":"),
                    ),
                    qos=1,
                    retain=True,
                )

            await asyncio.wait_for(client.start_notify(notify_uuid, on_notify), timeout=connect_timeout)
            notification_started = True
            while not STOP and client.is_connected:
                if asyncio.get_running_loop().time() - last_notification > stale_timeout:
                    raise TimeoutError(f"derailleur notifications stale for {stale_timeout:g}s")
                await asyncio.sleep(0.5)
            raise ConnectionError("derailleur disconnected")
        except Exception as exc:
            mqttc.publish(topics["status"], f"offline:{type(exc).__name__}", qos=1, retain=True)
        finally:
            if client is not None:
                if notification_started and client.is_connected:
                    try:
                        await asyncio.wait_for(client.stop_notify(notify_uuid), timeout=5)
                    except Exception:
                        pass
                if client.is_connected:
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5)
                    except Exception:
                        pass
        await asyncio.sleep(delay)
        if not real_data:
            delay = min(reconnect_max, delay * 2)

    mqttc.publish(topics["status"], "offline", qos=1, retain=True)
    mqttc.loop_stop()
    mqttc.disconnect()
    return 0


def main() -> int:
    parser = config_arg_parser("Publish HPR derailleur gear telemetry")
    args = parser.parse_args()

    def stop(*_args) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return asyncio.run(run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())

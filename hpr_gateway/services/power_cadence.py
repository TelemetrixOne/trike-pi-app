from __future__ import annotations

import asyncio
import json
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bleak import BleakClient, BleakScanner

from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.config import config_arg_parser, enabled_items, load_config
from hpr_gateway.mqtt import connect_client

UUID_CPM_CHAR = "00002a63-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"


class PedalNotFound(RuntimeError):
    pass


class PedalStreamStale(RuntimeError):
    pass


@dataclass
class CrankState:
    last_crank_revs: Optional[int] = None
    last_crank_event_time: Optional[int] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def calc_cadence_rpm(prev_revs: int, prev_time_1024: int, revs: int, time_1024: int) -> Optional[float]:
    d_revs = (revs - prev_revs) & 0xFFFF
    d_time = (time_1024 - prev_time_1024) & 0xFFFF
    if d_time == 0:
        return None
    rpm = (d_revs / (d_time / 1024.0)) * 60.0
    return rpm if 0 <= rpm <= 250 else None


def parse_cpm(data: bytes) -> dict:
    if len(data) < 4:
        raise ValueError(f"CPM too short: {len(data)} bytes")
    flags = struct.unpack_from("<H", data, 0)[0]
    power_w = struct.unpack_from("<h", data, 2)[0]
    offset = 4
    out = {"flags": flags, "power_w": int(power_w), "raw_len": len(data), "raw_hex": data.hex()}
    if flags & (1 << 0):
        offset += 1
    if flags & (1 << 2):
        offset += 2
    if flags & (1 << 4):
        offset += 6
    if flags & (1 << 5):
        if len(data) < offset + 4:
            raise ValueError("CPM missing crank revolution data")
        out["crank_revs"], out["crank_event_time"] = struct.unpack_from("<HH", data, offset)
    return out


async def bounded(awaitable, timeout_seconds: float, operation: str):
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{operation} timed out after {timeout_seconds:g}s") from exc


async def run_device(config, mqttc, name: str, device_cfg: dict) -> None:
    mac = device_cfg["mac"]
    topic_base = config.resolve_topic(device_cfg.get("topic_base"), "pedals", name).rstrip("/")
    battery_poll = float(device_cfg.get("battery_poll_seconds", 300))
    scan_timeout = float(device_cfg.get("scan_timeout_seconds", 10))
    connect_timeout = float(device_cfg.get("connect_timeout_seconds", 15))
    gatt_timeout = float(device_cfg.get("gatt_timeout_seconds", 8))
    stale_timeout = float(device_cfg.get("notification_stale_timeout_seconds", 15))
    reconnect_initial = float(device_cfg.get("reconnect_initial_seconds", 1))
    reconnect_max = float(device_cfg.get("reconnect_max_seconds", 15))
    adapter = resolve_bluetooth_role(
        config, "telemetry", legacy_adapter_path="power_cadence.bluetooth_adapter"
    )
    crank = CrankState()
    reconnect_delay = reconnect_initial

    def publish(topic: str, payload) -> None:
        msg = json.dumps(payload, separators=(",", ":")) if isinstance(payload, (dict, list)) else str(payload)
        mqttc.publish(topic, msg, qos=0, retain=False)

    while True:
        client = None
        notification_started = False
        real_data_seen = False
        try:
            dev = await bounded(
                BleakScanner.find_device_by_address(mac, timeout=scan_timeout, adapter=adapter),
                scan_timeout + 1,
                "BLE scan",
            )
            if not dev:
                publish(f"{topic_base}/status", {"ts": now_iso(), "state": "not_found", "mac": mac})
                raise PedalNotFound(f"{mac} was not found")
            publish(f"{topic_base}/status", {"ts": now_iso(), "state": "connecting", "mac": mac})
            client = BleakClient(dev, timeout=connect_timeout)
            await bounded(client.connect(), connect_timeout, "BLE connect")
            last_notification = time.monotonic()
            last_battery = 0.0

            def on_notify(_sender, payload) -> None:
                nonlocal last_notification, real_data_seen, reconnect_delay
                try:
                    parsed = parse_cpm(bytes(payload))
                except Exception as exc:
                    publish(
                        f"{topic_base}/status",
                        {"ts": now_iso(), "state": "invalid_notification", "err": str(exc)},
                    )
                    return

                last_notification = time.monotonic()
                real_data_seen = True
                reconnect_delay = reconnect_initial
                publish(f"{topic_base}/power_w", parsed["power_w"])
                if "crank_revs" in parsed:
                    if crank.last_crank_revs is not None and crank.last_crank_event_time is not None:
                        rpm = calc_cadence_rpm(
                            crank.last_crank_revs,
                            crank.last_crank_event_time,
                            parsed["crank_revs"],
                            parsed["crank_event_time"],
                        )
                        if rpm is not None:
                            publish(f"{topic_base}/cadence_rpm", round(rpm, 1))
                    crank.last_crank_revs = parsed["crank_revs"]
                    crank.last_crank_event_time = parsed["crank_event_time"]

            await bounded(
                client.start_notify(UUID_CPM_CHAR, on_notify),
                gatt_timeout,
                "GATT notification subscription",
            )
            notification_started = True
            publish(f"{topic_base}/status", {"ts": now_iso(), "state": "connected"})

            while client.is_connected:
                now = time.monotonic()
                if now - last_notification > stale_timeout:
                    raise PedalStreamStale(
                        f"no power/cadence notification for {stale_timeout:g}s"
                    )
                if now - last_battery > battery_poll:
                    last_battery = now
                    try:
                        battery = await bounded(
                            client.read_gatt_char(UUID_BATTERY_CHAR),
                            gatt_timeout,
                            "battery GATT read",
                        )
                        if battery:
                            publish(f"{topic_base}/battery_pct", int(battery[0]))
                    except Exception as exc:
                        publish(
                            f"{topic_base}/status",
                            {"ts": now_iso(), "state": "battery_read_failed", "err": str(exc)},
                        )
                await asyncio.sleep(min(1.0, stale_timeout / 2))

            raise ConnectionError("BLE client disconnected")
        except PedalNotFound:
            pass
        except Exception as exc:
            publish(f"{topic_base}/status", {"ts": now_iso(), "state": "disconnected", "err": str(exc)})
        finally:
            if client is not None:
                if notification_started and client.is_connected:
                    try:
                        await bounded(
                            client.stop_notify(UUID_CPM_CHAR),
                            gatt_timeout,
                            "GATT notification stop",
                        )
                    except Exception:
                        pass
                if client.is_connected:
                    try:
                        await bounded(client.disconnect(), gatt_timeout, "BLE disconnect")
                    except Exception:
                        pass

        await asyncio.sleep(reconnect_delay)
        if not real_data_seen:
            reconnect_delay = min(reconnect_max, reconnect_delay * 2)


async def run(config_path: str) -> int:
    config = load_config(config_path)
    mqttc = connect_client(config, f"hpr-{config.trike_id}-power-cadence")
    devices = list(enabled_items(config.get("power_cadence.devices", {})))
    await asyncio.gather(*(run_device(config, mqttc, name, cfg) for name, cfg in devices))
    return 0


def main() -> int:
    parser = config_arg_parser("Publish HPR BLE power/cadence telemetry")
    args = parser.parse_args()
    return asyncio.run(run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())

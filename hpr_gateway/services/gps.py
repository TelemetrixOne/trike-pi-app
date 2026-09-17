from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import gps

from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import connect_client

LOG = logging.getLogger("hpr_gps")
NAV_SCHEMA = "hpr.nav.v1"
NAV_SOURCE = "pi_gpsd"
REAL_GPS_REPORTS = {"TPV", "SKY", "GST", "ATT"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def report_value(report: Any, key: str, default: Any = None) -> Any:
    if isinstance(report, dict):
        return report.get(key, default)
    try:
        return report[key]
    except Exception:
        return getattr(report, key, default)


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_gps_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalise_degrees(value: Any) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return number % 360


def first_float(report: Any, *keys: str) -> float | None:
    for key in keys:
        value = safe_float(report_value(report, key))
        if value is not None:
            return value
    return None


def horizontal_accuracy_m(report: Any) -> float | None:
    values = [
        value
        for value in (
            safe_float(report_value(report, "eph")),
            safe_float(report_value(report, "epx")),
            safe_float(report_value(report, "epy")),
        )
        if value is not None
    ]
    return max(values) if values else None


def build_nav_payload(config: Any, report: Any, seq: int, published_at: datetime | None = None) -> dict[str, Any] | None:
    published_at = published_at or utc_now()
    lat = safe_float(report_value(report, "lat"))
    lon = safe_float(report_value(report, "lon"))
    if lat is None or lon is None:
        return None

    location_at = parse_gps_time(report_value(report, "time")) or published_at
    fix_age_ms = max(0, int((published_at - location_at).total_seconds() * 1000))

    speed_mps = safe_float(report_value(report, "speed"), 0.0)
    speed_kmh = round(speed_mps * 3.6, 3) if speed_mps is not None else None
    course_deg = normalise_degrees(report_value(report, "track"))
    fix_mode = safe_int(report_value(report, "mode"))

    payload = {
        "schema": NAV_SCHEMA,
        "source": NAV_SOURCE,
        "trike_id": config.trike_id,
        "seq": seq,
        "ts": utc_iso(location_at),
        "ts_location_utc": utc_iso(location_at),
        "ts_publish_utc": utc_iso(published_at),
        "fix_age_ms": fix_age_ms,
        "publish_latency_ms": fix_age_ms,
        "location_valid": fix_mode is None or fix_mode >= 2,
        "lat": lat,
        "lon": lon,
        "speed_mps": speed_mps,
        "speed_kmh": speed_kmh,
        "course_deg": course_deg,
        "bearing_deg": course_deg,
        "alt_m": safe_float(report_value(report, "alt")),
        "fix_mode": fix_mode,
        "accuracy_m": horizontal_accuracy_m(report),
        "vertical_accuracy_m": safe_float(report_value(report, "epv")),
        "speed_accuracy_mps": safe_float(report_value(report, "eps")),
        "course_accuracy_deg": first_float(report, "epd", "epc"),
        "bearing_accuracy_deg": first_float(report, "epd", "epc"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def close_session(session: Any) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def is_real_receiver_report(report_class: Any) -> bool:
    return str(report_class or "").upper() in REAL_GPS_REPORTS


def reconnect_delay(failure_count: int, initial_seconds: float, maximum_seconds: float) -> float:
    return min(initial_seconds * (2 ** max(0, failure_count - 1)), maximum_seconds)


def main() -> int:
    parser = config_arg_parser("Publish HPR GPS telemetry from gpsd")
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(level=getattr(logging, config.get("logging.level", "INFO").upper()))
    topic = config.resolve_topic(config.get("gps.topic"), "nav")
    status_topic = config.resolve_topic(config.get("gps.status_topic"), "nav", "status")
    stale_timeout = float(config.get("gps.stale_timeout_seconds", 5.0))
    wait_timeout = float(config.get("gps.wait_timeout_seconds", 0.5))
    reconnect_initial = float(config.get("gps.reconnect_initial_seconds", 1.0))
    reconnect_max = float(config.get("gps.reconnect_max_seconds", 15.0))
    client = connect_client(
        config,
        f"hpr-{config.trike_id}-gps",
        will_topic=status_topic,
        will_payload=json.dumps({"state": "offline", "reason": "mqtt_last_will"}),
        will_qos=1,
        will_retain=True,
    )
    client.publish(status_topic, payload=json.dumps({"state": "starting"}), qos=1, retain=True)
    seq = 0
    session = None
    last_real_report = 0.0
    consecutive_failures = 0
    receiver_active = False
    while True:
        try:
            if session is None:
                LOG.info("opening gpsd session")
                session = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
                # A new session gets a full grace period. Reusing an old
                # timestamp causes an immediate reopen loop after USB loss.
                last_real_report = time.monotonic()
                receiver_active = False
                client.publish(
                    status_topic,
                    payload=json.dumps({"state": "connecting", "reason": "gpsd_opened"}),
                    qos=1,
                    retain=True,
                )

            if time.monotonic() - last_real_report > stale_timeout:
                raise TimeoutError("no real GPS receiver data")

            if not session.waiting(timeout=wait_timeout):
                continue

            report = session.next()
            report_class = report_value(report, "class")
            # VERSION/WATCH/DEVICES are gpsd handshakes, not proof that the
            # receiver survived a USB reconnect.
            if is_real_receiver_report(report_class):
                last_real_report = time.monotonic()
                if not receiver_active:
                    LOG.info("real GPS receiver data resumed")
                    client.publish(
                        status_topic,
                        payload=json.dumps({"state": "online", "reason": "receiver_data"}),
                        qos=1,
                        retain=True,
                    )
                    receiver_active = True
                if consecutive_failures:
                    LOG.info("GPS stream recovered after %d attempt(s)", consecutive_failures)
                    consecutive_failures = 0

            if report_class != "TPV":
                continue

            payload = build_nav_payload(config, report, seq + 1)
            if payload is None:
                continue
            seq += 1
            client.publish(topic, payload=json.dumps(payload, separators=(",", ":")), qos=0, retain=False)
        except Exception as exc:
            LOG.warning("gps loop failed: %s", exc)
            client.publish(status_topic, payload=json.dumps({"state": "stale", "reason": type(exc).__name__}), qos=1, retain=True)
            close_session(session)
            session = None
            receiver_active = False
            consecutive_failures += 1
            delay = reconnect_delay(consecutive_failures, reconnect_initial, reconnect_max)
            LOG.warning("GPS recovery attempt %d in %.1fs", consecutive_failures, delay)
            time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())

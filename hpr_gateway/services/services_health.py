from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone

from hpr_gateway.config import config_arg_parser, enabled_items, load_config
from hpr_gateway.mqtt import encode_payload, make_client


def service_health(unit: str) -> str:
    result = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True)
    state = result.stdout.strip()
    if state == "active":
        return "Good"
    if state in {"inactive", "failed", "activating", "deactivating", "not-found"}:
        return "Stopped"
    return "Unknown"


def derive_device_health(process: str, device_state: str | None) -> tuple[str, str, str]:
    """Return legacy overall state, data state and an operator-facing reason."""
    if process != "Good":
        return process, "unknown", "service_not_running"
    state = (device_state or "unknown").lower()
    if state == "online":
        return "Good", "fresh", "receiver_data"
    if state in {"starting", "connecting"}:
        return "Degraded", "waiting", "waiting_for_receiver_data"
    if state in {"stale", "offline"}:
        return "Failed", "stale", "no_receiver_data"
    return "Degraded", "unknown", "device_status_unknown"


class DeviceStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[object, float]] = {}

    def update(self, topic: str, payload: bytes) -> None:
        try:
            raw = payload.decode("utf-8")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw.strip()
        except UnicodeDecodeError:
            value = "invalid_status_payload"
        with self._lock:
            self._values[topic] = (value, time.monotonic())

    def get(self, topic: str) -> tuple[object | None, float | None]:
        with self._lock:
            return self._values.get(topic, (None, None))


def state_from(value: object | None) -> str:
    if isinstance(value, dict):
        return str(value.get("state", "unknown")).lower()
    return str(value or "unknown").lower()


def reason_from(value: object | None) -> str | None:
    return str(value.get("reason")) if isinstance(value, dict) and value.get("reason") else None


def age_seconds(received: float | None) -> float | None:
    return round(time.monotonic() - received, 1) if received is not None else None


def sensor_overall(process: str, race_enabled: bool, good: bool, waiting: bool = False) -> str:
    if process != "Good":
        return process
    if not race_enabled:
        return "Disabled"
    if good:
        return "Good"
    return "Degraded" if waiting else "Failed"


def legacy_overall(overall: str) -> str:
    """Keep existing Pit View cards quiet until their Disabled styling is deployed."""
    return "Good" if overall == "Disabled" else overall


def selected_tpms_names(assignment: object, available: set[str]) -> tuple[list[str], str | None]:
    """Validate HA's authoritative three-wheel to physical-sensor assignment."""
    if not isinstance(assignment, dict):
        return [], "assignment_unavailable"
    names = [str(assignment.get(position, "")).lower() for position in ("front_left", "front_right", "rear")]
    if any(not name for name in names) or len(set(names)) != 3:
        return [], "assignment_invalid_or_duplicated"
    if any(name not in available for name in names):
        return [], "assignment_references_unconfigured_sensor"
    return names, None


def subscribe_source_topics(client: object, source_topics: list[str]) -> None:
    """Restore health evidence subscriptions after every MQTT connection."""
    for source_topic in source_topics:
        client.subscribe(source_topic, qos=1)


def main() -> int:
    parser = config_arg_parser("Publish HPR systemd service health")
    args = parser.parse_args()
    config = load_config(args.config)
    service = config.get("services.services_health", {})
    interval = float(service.get("interval_seconds", 5))
    units = service.get("units", {})
    base_topic = config.resolve_topic(config.get("health.base_topic"), "pi")
    gps_status_topic = config.resolve_topic(config.get("gps.status_topic"), "nav", "status")
    race_mode_topic = f"hpr/{config.trike_id}/agents/health/enabled/state"
    hr_status_topic = f"hpr/{config.trike_id}/hr/status"
    hr_data_topic = f"hpr/{config.trike_id}/hr/json"
    tpms_topics_by_name = {
        name.lower(): f"{config.resolve_topic(sensor.get('topic'), 'tpms', name).rstrip('/')}/availability"
        for name, sensor in enabled_items(config.get("tpms.sensors", {}))
    }
    tpms_assignment_topic = f"hpr/{config.trike_id}/config/tpms_assignments"
    tpms_fresh_seconds = float(config.get("services.tpms.stale_seconds", 300)) + interval
    pedal_devices = list(enabled_items(config.get("power_cadence.devices", {})))
    pedal_status_topics = [
        f"{config.resolve_topic(device.get('topic_base'), 'pedals', name).rstrip('/')}/status"
        for name, device in pedal_devices
    ]
    pedal_data_topics = [topic.removesuffix("/status") + "/power_w" for topic in pedal_status_topics]
    source_topics = [
        race_mode_topic,
        gps_status_topic,
        hr_status_topic,
        hr_data_topic,
        tpms_assignment_topic,
        *tpms_topics_by_name.values(),
        *pedal_status_topics,
        *pedal_data_topics,
    ]
    statuses = DeviceStatus()
    client = make_client(config, f"hpr-{config.trike_id}-service-health")
    client.on_message = lambda _client, _userdata, message: statuses.update(message.topic, message.payload)

    def on_connect(connected_client, _userdata, _flags, reason_code, _properties=None) -> None:
        failed = reason_code.is_failure if hasattr(reason_code, "is_failure") else reason_code != 0
        if not failed:
            subscribe_source_topics(connected_client, source_topics)

    client.on_connect = on_connect
    client.connect(config.mqtt.host, config.mqtt.port, keepalive=60)
    client.loop_start()
    while True:
        for topic_suffix, unit in units.items():
            topic = f"{base_topic}/{topic_suffix}"
            process = service_health(unit)
            race_value, _ = statuses.get(race_mode_topic)
            race_enabled = str(race_value or "OFF").upper() == "ON"
            if topic_suffix == "gps_health":
                source, received = statuses.get(gps_status_topic)
                device = state_from(source)
                source_reason = reason_from(source)
                _observed, data, reason = derive_device_health(process, device)
                overall = sensor_overall(process, race_enabled, device == "online",
                                         device in {"unknown", "starting", "connecting"})
                age = age_seconds(received)
                detail = {
                    "schema": "hpr.service_health.v2",
                    "trike_id": config.trike_id,
                    "service": "gps",
                    "process": "running" if process == "Good" else process.lower(),
                    "device": device or "unknown",
                    "data": data,
                    "overall": overall,
                    "reason": reason,
                    "source_reason": source_reason,
                    "status_age_seconds": age,
                    "expected": race_enabled,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
                client.publish(topic, payload=legacy_overall(overall), qos=1, retain=True)
                client.publish(f"{base_topic}/gps_detail", payload=encode_payload(detail), qos=1, retain=True)
            elif topic_suffix == "tpms_health":
                assignment, _ = statuses.get(tpms_assignment_topic)
                selected_names, assignment_error = selected_tpms_names(assignment, set(tpms_topics_by_name))
                selected_topics = [tpms_topics_by_name[name] for name in selected_names]
                readings = [(state_from(statuses.get(t)[0]), age_seconds(statuses.get(t)[1])) for t in selected_topics]
                all_online = not assignment_error and len(readings) == 3 and all(
                    state == "online" and age is not None and age <= tpms_fresh_seconds for state, age in readings
                )
                overall = sensor_overall(process, race_enabled, all_online, bool(race_enabled and assignment_error))
                reason = assignment_error or ("all_selected_sensors_reporting" if all_online else "one_or_more_selected_sensors_not_reporting")
                detail = {"schema": "hpr.service_health.v2", "trike_id": config.trike_id,
                          "service": "tpms", "process": "running" if process == "Good" else process.lower(),
                          "device": "online" if all_online else "missing_sensor", "data": "fresh" if all_online else "stale",
                          "overall": overall, "reason": reason, "expected": race_enabled,
                          "assignment": assignment if isinstance(assignment, dict) else None,
                          "sensors": [{"name": name, "topic": t, "state": s, "age_seconds": a}
                                      for name, t, (s, a) in zip(selected_names, selected_topics, readings)],
                          "observed_at": datetime.now(timezone.utc).isoformat()}
                client.publish(topic, payload=legacy_overall(overall), qos=1, retain=True)
                client.publish(f"{base_topic}/tpms_detail", payload=encode_payload(detail), qos=1, retain=True)
            elif topic_suffix == "power_cadence_health":
                pedal_states = [state_from(statuses.get(t)[0]) for t in pedal_status_topics]
                data_ages = [age_seconds(statuses.get(t)[1]) for t in pedal_data_topics]
                fresh = bool(data_ages) and all(age is not None and age <= 20 for age in data_ages)
                connected = bool(pedal_states) and all(state == "connected" for state in pedal_states)
                overall = sensor_overall(process, race_enabled, fresh, connected and not fresh)
                detail = {"schema": "hpr.service_health.v2", "trike_id": config.trike_id,
                          "service": "power_cadence", "process": "running" if process == "Good" else process.lower(),
                          "device": "connected" if connected else "disconnected", "data": "fresh" if fresh else "stale",
                          "overall": overall, "reason": "power_notifications_fresh" if fresh else "no_recent_power_notifications",
                          "expected": race_enabled, "devices": [{"status_topic": s, "state": state, "data_age_seconds": age}
                          for s, state, age in zip(pedal_status_topics, pedal_states, data_ages)],
                          "observed_at": datetime.now(timezone.utc).isoformat()}
                client.publish(topic, payload=legacy_overall(overall), qos=1, retain=True)
                client.publish(f"{base_topic}/power_cadence_detail", payload=encode_payload(detail), qos=1, retain=True)
            elif topic_suffix == "heart_rate_health":
                hr_status_value, _ = statuses.get(hr_status_topic)
                hr_data, hr_received = statuses.get(hr_data_topic)
                hr_state = state_from(hr_status_value).split(":", 1)[0]
                hr_age = age_seconds(hr_received)
                bpm = hr_data.get("bpm") if isinstance(hr_data, dict) else None
                fresh = isinstance(bpm, (int, float)) and bpm > 0 and hr_age is not None and hr_age <= 25
                connected = hr_state in {"connected", "active"}
                overall = sensor_overall(process, race_enabled, fresh, connected and not fresh)
                detail = {"schema": "hpr.service_health.v2", "trike_id": config.trike_id,
                          "service": "heart_rate", "process": "running" if process == "Good" else process.lower(),
                          "device": "connected" if connected else hr_state, "data": "fresh" if fresh else "stale",
                          "overall": overall, "reason": "valid_bpm_fresh" if fresh else "no_recent_valid_bpm",
                          "expected": race_enabled, "bpm": bpm, "data_age_seconds": hr_age,
                          "observed_at": datetime.now(timezone.utc).isoformat()}
                client.publish(topic, payload=legacy_overall(overall), qos=1, retain=True)
                client.publish(f"{base_topic}/heart_rate_detail", payload=encode_payload(detail), qos=1, retain=True)
            else:
                client.publish(topic, payload=process, qos=1, retain=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

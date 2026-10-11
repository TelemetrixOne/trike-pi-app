#!/usr/bin/env python3
"""Reproduce GPIO audit findings with fake devices; no GPIO, MQTT or SSH I/O.

This is a diagnostic snapshot, not an assertion that the live Pi caused these
symptoms. Run from any directory with the repository's Python dependencies.
"""
from __future__ import annotations

import copy
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hpr_gateway import gpio_profile
from hpr_gateway.services import gpio_control as gpio
from hpr_gateway.services.gpio_config_sync import ProfileApplier as GpioApplier
from hpr_gateway.services.trike_config_sync import ProfileApplier as TrikeApplier
from hpr_gateway.services.trike_config_sync import profile_hash as trike_hash


def idle_auto_activity():
    service = object.__new__(gpio.GPIOControlService)
    config = gpio.CircuitConfig("test", "headlight", "", "", 5, "AUTO", True,
                                control_type="headlight_mode")
    output = Mock()
    runtime = gpio.CircuitRuntime(config, output, None, None, None,
                                 "OFF", "ON", "AUTO", mode="AUTO")
    service._ambient_auto_enabled = lambda _runtime: True
    service._ambient_auto_state = lambda _runtime: "ON"
    service._publish_runtime = Mock()
    for _ in range(300):
        service._apply_logic(runtime, publish=False)
    return {"unchanged_logic_evaluations": 300, "output_on_calls": output.on.call_count,
            "state_publications": service._publish_runtime.call_count,
            "nominal_duration_at_default_poll_seconds": 60}


def duplicate_drs_activity():
    service = object.__new__(gpio.GPIOControlService)
    service._drs_lock = threading.RLock()
    service._drs = gpio.DRSRuntime(12, 333, 1000, 1500, output=Mock(),
                                  state="CLOSE", current_pulse_us=1000)
    with patch.object(gpio, "DRS", {"transition_seconds": 0.5}), \
         patch.object(gpio.time, "sleep") as sleeper:
        for _ in range(2):
            service._set_drs("CLOSE", publish=False)
    return {"already_closed_commands": 2, "transition_sleep_calls": sleeper.call_count,
            "requested_callback_block_seconds": round(sum(c.args[0] for c in sleeper.call_args_list), 3)}


def retained_commands():
    service = object.__new__(gpio.GPIOControlService)
    service._drs = gpio.DRSRuntime(12, 333, 1000, 1500)
    service._set_drs_mode = Mock()
    config = gpio.CircuitConfig("test", "horn", "", "", 17, "OFF", False)
    horn = gpio.CircuitRuntime(config, Mock(), None, None, None, "OFF", "OFF", "REMOTE")
    service._circuits = {"test:horn": horn}
    service._apply_logic = Mock()
    with patch.object(gpio, "MQTT", {"topic_root": "test"}), \
         patch.object(gpio, "DRS", {"trike": "test"}):
        service._handle_message(SimpleNamespace(
            topic="test/test/control/drs/set", payload=b"OPEN", retain=True))
        service._handle_message(SimpleNamespace(
            topic="test/test/control/horn/set", payload=b"ON", retain=True))
    return {"retained_drs_open_accepted": service._set_drs_mode.called,
            "retained_horn_on_accepted": horn.requested_state == "ON"}


def adc_busy_wait():
    # Call the installed driver's real read algorithm against a simulated ADC.
    # Completion is deliberately released after 50 ms so the audit always ends.
    from adafruit_ads1x15.ads1x15 import ADS1x15, Mode
    polls = 0
    started = time.perf_counter()
    cpu_started = time.process_time()

    def complete():
        nonlocal polls
        polls += 1
        return time.perf_counter() - started >= 0.05

    device = SimpleNamespace(mode=Mode.SINGLE, _last_pin_read=None,
                             _write_config=lambda _pin: None,
                             _conversion_complete=complete,
                             get_last_result=lambda _fast: 1,
                             _conversion_value=lambda value: value)
    ADS1x15._read(device, 0)
    return {"driver_version": importlib.metadata.version("adafruit-circuitpython-ads1x15"),
            "fake_conversion_wall_seconds": round(time.perf_counter() - started, 4),
            "process_cpu_seconds": round(time.process_time() - cpu_started, 4),
            "conversion_status_polls": polls,
            "note": "Fake I2C has no bus delay; poll count is not a measured Pi I2C rate."}


def timing_validation():
    profile = {"service": {"poll_interval_seconds": 0},
               "telemetry": {"sample_interval_seconds": 0, "retry_interval_seconds": 0}}
    try:
        gpio_profile.validate_gpio(profile)
    except ValueError:
        accepted = False
    else:
        accepted = True
    return {"zero_poll_sample_and_retry_intervals_accepted": accepted}


def disconnected_mqtt_queue():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="offline-audit-only")
    for _ in range(1000):
        client.publish("offline-audit/state", "ON", qos=1, retain=True)
    return {"network_connect_called": False, "configured_queue_limit": client.max_queued_messages,
            "pending_messages_after_1000_disconnected_publishes": len(client._out_messages)}


def competing_profiles():
    root = Path(__file__).resolve().parents[1]
    raw = (root / "config/hpr.example.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw.replace("CHANGE_ME", "offline-test"))
    for name in ("heart_rate", "tpms", "power_cadence", "derailleur", "gpio_control", "video"):
        data["services"].setdefault(name, {})["enabled"] = False
    data["gpio"] = {"enabled": False, "profile_version": "initial"}
    data["bluetooth"]["roles"]["telemetry"]["controller_address"] = "AA:BB:CC:DD:EE:01"
    data["bluetooth"]["roles"]["heart_rate"]["controller_address"] = "AA:BB:CC:DD:EE:02"
    trike = data["hpr"]["trike_id"]
    gpio_a = {"enabled": False, "profile_version": "profile-a"}
    gpio_b = {"enabled": False, "profile_version": "profile-b"}
    full = {"schema_version": 1, "trike_id": trike,
            "heart_rate": {"enabled": False, "monitors": data["heart_rate"]["monitors"]},
            "derailleur": {"enabled": False},
            "tpms": {"tpms_enabled": False, "sensors": data["tpms"]["sensors"]},
            "power_cadence": {"power_cadence_enabled": False, "devices": data["power_cadence"]["devices"]},
            "video": {"enabled": False}, "gpio": {"gpio_enabled": False, "gpio": gpio_a}}
    narrow = {"schema_version": 1, "trike_id": trike, "gpio_enabled": False, "gpio": gpio_b}
    controller = Mock()
    controller.active.return_value = False
    with tempfile.TemporaryDirectory(prefix="hpr-gpio-offline-") as temporary:
        path = Path(temporary) / "hpr.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        GpioApplier(path, controller).apply({"hash": gpio_profile.profile_hash(narrow), "profile": narrow})
        after_gpio = yaml.safe_load(path.read_text())['gpio']['profile_version']
        TrikeApplier(str(path), Mock()).apply({"hash": trike_hash(full), "profile": copy.deepcopy(full)})
        after_full = yaml.safe_load(path.read_text())['gpio']['profile_version']
    return {"after_gpio_profile": after_gpio, "after_differing_full_trike_profile": after_full,
            "note": "Both envelopes are synthetic and valid; live central payloads are unverified."}


def main():
    root = Path(__file__).resolve().parents[1]
    results = {"source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "hardware_access": False, "broker_access": False,
        "idle_auto": idle_auto_activity(), "duplicate_drs": duplicate_drs_activity(),
        "retained_commands": retained_commands(), "adc_busy_wait": adc_busy_wait(),
        "timing_validation": timing_validation(), "mqtt_offline_queue": disconnected_mqtt_queue(),
        "competing_profiles": competing_profiles()}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""HPR GPIO control service.

Features
- Reads local physical switch inputs.
- Drives output pins for trike circuits.
- Publishes MQTT state topics and Home Assistant discovery.
- Supports ADS1115 telemetry for ambient light, battery voltage, current,
  power, Ah used, and SOC.
- Publishes additional engineer-provided sense inputs as separate binary sensors.
- Keeps GPIO control alive if telemetry hardware or libraries are unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

import paho.mqtt.client as mqtt
from gpiozero import Button, OutputDevice, PWMOutputDevice

from hpr_gateway.config import HprConfig, config_arg_parser, enabled_items, load_config

MQTT: dict = {}
SERVICE: dict = {}
TELEMETRY: dict = {}
ADC: dict = {}
INPUTS: dict = {}
TRIKES: dict = {}
DRS: dict = {}
AMBIENT_AUTO: dict = {}

LOG = logging.getLogger("hpr_gpio_control")

STATE_ON = "ON"
STATE_OFF = "OFF"

SOURCE_LOCAL = "LOCAL"
SOURCE_REMOTE = "REMOTE"
SOURCE_AUTO = "AUTO"

MODE_BINARY = "binary"
MODE_THREE_POSITION = "three_position"

POSITION_ON = "ON"
POSITION_AUTO = "AUTO"
POSITION_OFF = "OFF"

HORN_REMOTE_PULSE_SECONDS = 1.0


def configure(config: HprConfig) -> None:
    global MQTT, SERVICE, TELEMETRY, ADC, INPUTS, TRIKES, DRS, AMBIENT_AUTO, HORN_REMOTE_PULSE_SECONDS
    gpio = config.get("gpio", {}) or {}
    service = gpio.get("service", {}) or {}
    telemetry = gpio.get("telemetry", {}) or {}
    adc = gpio.get("adc", {}) or {}
    ambient_light = gpio.get("ambient_light", {}) or {}
    calibration = ambient_light.get("calibration", {}) or {}

    MQTT = {
        "host": config.mqtt.host,
        "port": config.mqtt.port,
        "username": config.mqtt.username,
        "password": config.mqtt.password,
        "client_id": f"{config.trike_id}_control_gateway",
        "discovery_prefix": str(service.get("discovery_prefix", "homeassistant")),
        "topic_root": config.topic_root,
        "qos": int(service.get("qos", 1)),
        "retain_state": bool(service.get("retain_state", True)),
        "retain_discovery": bool(service.get("retain_discovery", True)),
    }
    SERVICE = {
        "discovery_enabled": bool(service.get("discovery_enabled", True)),
        "publish_discovery_on_start": bool(service.get("publish_discovery_on_start", True)),
        "republish_state_on_reconnect": bool(service.get("republish_state_on_reconnect", True)),
        "poll_interval_seconds": float(service.get("poll_interval_seconds", 0.2)),
        "log_level": str(config.get("logging.level", "INFO")),
    }
    TELEMETRY = {
        "enabled": bool(telemetry.get("enabled", False)),
        "trike": config.trike_id,
        "sample_interval_seconds": float(telemetry.get("sample_interval_seconds", 1.0)),
        "moving_average_samples": int(telemetry.get("moving_average_samples", 5)),
        "retry_interval_seconds": float(telemetry.get("retry_interval_seconds", 10.0)),
    }
    ADC = {
        "i2c_address": adc.get("i2c_address", "0x48"),
        "ads_gain": int(adc.get("ads_gain", 1)),
        "shunt_p": adc.get("shunt_p", "P0"),
        "shunt_n": adc.get("shunt_n", "P1"),
        "battery_channel": adc.get("battery_channel", "P2"),
        "light_channel": adc.get("light_channel", "P3"),
        "shunt_resistance_ohm": float(adc.get("shunt_resistance_ohm", 0.0025)),
        "battery_divider_r1_ohm": float(adc.get("battery_divider_r1_ohm", 68000.0)),
        "battery_divider_r2_ohm": float(adc.get("battery_divider_r2_ohm", 9840.0)),
        "battery_capacity_ah": float(adc.get("battery_capacity_ah", 5.0)),
        "battery_full_voltage": float(adc.get("battery_full_voltage", 20.6)),
        "battery_empty_voltage": float(adc.get("battery_empty_voltage", 15.5)),
        "light_dark_voltage": float(calibration.get("dark_voltage", adc.get("light_dark_voltage", 1.95))),
        "light_bright_voltage": float(calibration.get("light_voltage", adc.get("light_bright_voltage", 3.13))),
        "adc_reference_voltage": float(adc.get("adc_reference_voltage", 3.3)),
        "light_sensor_invert": bool(adc.get("light_sensor_invert", False)),
        "discharge_only": bool(adc.get("discharge_only", True)),
        "negative_current_noise_floor_a": float(adc.get("negative_current_noise_floor_a", 0.1)),
        "soc_coulomb_weight": float(adc.get("soc_coulomb_weight", 0.85)),
        "soc_voltage_weight": float(adc.get("soc_voltage_weight", 0.15)),
    }
    AMBIENT_AUTO = ambient_light.get("automatic_headlights", {}) or {}

    circuits = {}
    for name, item in enabled_items(gpio.get("circuits", {})):
        circuits[name] = {
            "enabled": True,
            "output_pin": int(item.get("output_pin", item.get("pin"))),
            "boot_default": str(item.get("boot_default", "OFF")),
            "output_active_high": bool(item.get("output_active_high", True)),
            "icon": str(item.get("icon", "mdi:toggle-switch")),
            "friendly_name": str(item.get("friendly_name", name.replace("_", " ").title())),
            "switch_mode": str(item.get("switch_mode", "binary")),
            "switch_pin": item.get("switch_pin"),
            "switch_pull_up": item.get("switch_pull_up"),
            "switch_active_high": bool(item.get("switch_active_high", True)),
            "switch_pin_on": item.get("switch_pin_on"),
            "switch_pin_auto": item.get("switch_pin_auto"),
            "three_position_pull_up": item.get("three_position_pull_up"),
            "three_position_active_high": bool(item.get("three_position_active_high", True)),
            "auto_enabled": bool(item.get("auto_enabled", False)),
            "auto_on_below_pct": item.get("auto_on_below_pct"),
            "auto_off_above_pct": item.get("auto_off_above_pct"),
        }
    TRIKES = {config.trike_id: {"enabled": True, "circuits": circuits}}

    inputs = {}
    for name, item in enabled_items(gpio.get("sense_inputs", {})):
        inputs[name] = {
            "enabled": True,
            "pin": int(item["pin"]),
            "pull_up": item.get("pull_up"),
            "active_high": bool(item.get("active_high", True)),
            "state_semantics": item.get("state_semantics", "legacy"),
            "friendly_name": str(item.get("friendly_name", name.replace("_", " ").title())),
            "icon": str(item.get("icon", "mdi:electric-switch")),
        }
    INPUTS = {config.trike_id: inputs}
    drs = (gpio.get("pwm_outputs", {}) or {}).get("drs", {}) or {}
    DRS = {"enabled": bool(drs.get("enabled", False)), "trike": config.trike_id,
           "pin": drs.get("pin"), "frequency_hz": drs.get("frequency_hz"),
           "close_pulse_us": drs.get("close_pulse_us"), "open_pulse_us": drs.get("open_pulse_us"),
           "switch_behavior": drs.get("switch_behavior", "remote_open_close"),
           "transition_seconds": drs.get("transition_seconds", 0),
           "pulse_release_seconds": drs.get("pulse_release_seconds")}
    HORN_REMOTE_PULSE_SECONDS = float(gpio.get("horn_remote_pulse_seconds", 1.0))


@dataclass
class CircuitConfig:
    trike: str
    name: str
    friendly_name: str
    icon: str
    output_pin: int
    boot_default: str
    output_active_high: bool

    switch_mode: str = MODE_BINARY

    switch_pin: Optional[int] = None
    switch_pull_up: Optional[bool] = True
    switch_active_high: bool = False

    switch_pin_on: Optional[int] = None
    switch_pin_auto: Optional[int] = None
    three_position_pull_up: Optional[bool] = True
    three_position_active_high: bool = False

    auto_enabled: bool = False
    auto_on_below_pct: Optional[float] = None
    auto_off_above_pct: Optional[float] = None


@dataclass
class CircuitRuntime:
    config: CircuitConfig
    output: OutputDevice
    switch: Optional[Button]
    switch_on: Optional[Button]
    switch_auto: Optional[Button]
    requested_state: str
    effective_state: str
    source: str
    remote_pulse_until_monotonic: Optional[float] = None
    ambient_candidate_state: Optional[str] = None
    ambient_candidate_since_monotonic: Optional[float] = None


@dataclass
class DRSRuntime:
    pin: int
    frequency_hz: int
    close_pulse_us: int
    open_pulse_us: int
    output: Optional[PWMOutputDevice] = None
    state: str = "CLOSE"
    current_pulse_us: Optional[float] = None
    last_switch_state: Optional[str] = None
    auto_latched_on: bool = False
    pulse_release_at_monotonic: Optional[float] = None


@dataclass
class SenseInputConfig:
    trike: str
    name: str
    friendly_name: str
    icon: str
    pin: int
    pull_up: Optional[bool]
    active_high: bool
    state_semantics: str = "legacy"


@dataclass
class SenseInputRuntime:
    config: SenseInputConfig
    button: Button
    last_state: Optional[str] = None


class TelemetryMonitor:
    def __init__(self, mqtt_client: mqtt.Client, publish_fn) -> None:
        self._client = mqtt_client
        self._publish = publish_fn

        self._enabled = bool(TELEMETRY["enabled"])
        self._retry_interval_sec = float(TELEMETRY["retry_interval_seconds"])
        self._sample_interval_sec = float(TELEMETRY["sample_interval_seconds"])
        self._last_attempt_monotonic = 0.0
        self._next_sample_due_monotonic = 0.0

        self._hardware_ready = False
        self._ads = None
        self._shunt_diff = None
        self._battery_chan = None
        self._light_chan = None

        ma = int(TELEMETRY["moving_average_samples"])
        self._battery_voltage_samples: Deque[float] = deque(maxlen=ma)
        self._current_samples: Deque[float] = deque(maxlen=ma)
        self._light_samples: Deque[float] = deque(maxlen=ma)
        self._light_voltage_samples: Deque[float] = deque(maxlen=ma)

        self._last_time_monotonic = time.monotonic()
        self._ah_used = 0.0
        self._remaining_ah = float(ADC["battery_capacity_ah"])
        self._last_published: Dict[str, str] = {}
        self._last_init_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _resolve_ads_channel(value) -> int:
        if isinstance(value, int):
            if value in (0, 1, 2, 3):
                return value
            raise ValueError(f"ADS channel integer must be 0..3, got {value}")
        s = str(value).strip().upper()
        mapping = {
            "0": 0, "1": 1, "2": 2, "3": 3,
            "P0": 0, "P1": 1, "P2": 2, "P3": 3,
            "AIN0": 0, "AIN1": 1, "AIN2": 2, "AIN3": 3,
        }
        if s not in mapping:
            raise ValueError(f"Unsupported ADS channel value: {value!r}")
        return mapping[s]

    def battery_voltage_from_adc(self, v_adc: float) -> float:
        r1 = float(ADC["battery_divider_r1_ohm"])
        r2 = float(ADC["battery_divider_r2_ohm"])
        return v_adc * ((r1 + r2) / r2)

    def shunt_current_from_voltage(self, v_shunt: float) -> float:
        return v_shunt / float(ADC["shunt_resistance_ohm"])

    def soc_from_voltage(self, v_batt: float) -> float:
        batt_full = float(ADC["battery_full_voltage"])
        batt_empty = float(ADC["battery_empty_voltage"])
        if batt_full <= batt_empty:
            raise ValueError("battery_full_voltage must be greater than battery_empty_voltage")
        soc = 100.0 * (v_batt - batt_empty) / (batt_full - batt_empty)
        return self._clamp(soc, 0.0, 100.0)

    def light_percent_from_voltage(self, v_light: float) -> float:
        if "light_dark_voltage" in ADC and "light_bright_voltage" in ADC:
            dark_v = float(ADC["light_dark_voltage"])
            bright_v = float(ADC["light_bright_voltage"])
            if bright_v <= dark_v:
                raise ValueError("light_bright_voltage must be greater than light_dark_voltage")
            pct = 100.0 * (v_light - dark_v) / (bright_v - dark_v)
            return self._clamp(pct, 0.0, 100.0)

        ref_v = float(ADC["adc_reference_voltage"])
        pct = 100.0 * v_light / ref_v
        pct = self._clamp(pct, 0.0, 100.0)
        if bool(ADC.get("light_sensor_invert", False)):
            pct = 100.0 - pct
        return pct

    def _topic_root(self) -> str:
        return f"{MQTT['topic_root']}/{TELEMETRY['trike']}/control/telemetry"

    def latest_light_percent(self) -> Optional[float]:
        if not self._light_samples:
            return None
        return sum(self._light_samples) / len(self._light_samples)

    def latest_light_voltage(self) -> Optional[float]:
        if not self._light_voltage_samples:
            return None
        return sum(self._light_voltage_samples) / len(self._light_voltage_samples)

    def _publish_discovery(self) -> None:
        if not self._enabled or not SERVICE["discovery_enabled"]:
            return

        trike = TELEMETRY["trike"]
        device = {
            "identifiers": [f"{trike}_control_telemetry"],
            "name": f"{trike.upper()} Control Telemetry",
            "manufacturer": "HPR",
            "model": "GPIO Control Telemetry",
        }

        sensors = {
            "battery_voltage": ("Battery Voltage", "voltage", "V", "mdi:battery-high"),
            "current_a": ("Battery Current", "current", "A", "mdi:current-dc"),
            "power_w": ("Battery Power", "power", "W", "mdi:flash"),
            "ah_used": ("Battery Ah Used", None, "Ah", "mdi:battery-minus"),
            "soc": ("Battery SOC", "battery", "%", "mdi:battery"),
            "ambient_light_voltage": ("Ambient Light Voltage", "voltage", "V", "mdi:brightness-6"),
            "ambient_light_pct": ("Ambient Light", None, "%", "mdi:brightness-6"),
        }

        for key, (name, device_class, unit, icon) in sensors.items():
            object_id = f"{trike}_control_telemetry_{key}"
            payload = {
                "name": name,
                "object_id": object_id,
                "unique_id": object_id,
                "state_topic": f"{self._topic_root()}/{key}",
                "device": device,
                "icon": icon,
            }
            if device_class:
                payload["device_class"] = device_class
            if unit:
                payload["unit_of_measurement"] = unit
            self._publish(
                f"{MQTT['discovery_prefix']}/sensor/{object_id}/config",
                json.dumps(payload),
                retain=MQTT["retain_discovery"],
            )

    def start(self) -> None:
        if not self._enabled:
            LOG.info("Telemetry disabled in config")
            return
        self._publish_discovery()
        self._try_initialise(force=True)

    def _try_initialise(self, force: bool = False) -> None:
        if not self._enabled:
            return

        now = time.monotonic()
        if not force and (now - self._last_attempt_monotonic) < self._retry_interval_sec:
            return
        self._last_attempt_monotonic = now

        try:
            import board  # type: ignore
            import busio  # type: ignore
            import adafruit_ads1x15.ads1115 as ADS  # type: ignore
            from adafruit_ads1x15.analog_in import AnalogIn  # type: ignore

            i2c = busio.I2C(board.SCL, board.SDA)
            ads = ADS.ADS1115(
                i2c,
                address=int(ADC["i2c_address"], 0) if isinstance(ADC["i2c_address"], str) else int(ADC["i2c_address"]),
            )
            ads.gain = int(ADC["ads_gain"])

            shunt_p = self._resolve_ads_channel(ADC["shunt_p"])
            shunt_n = self._resolve_ads_channel(ADC["shunt_n"])
            battery_ch = self._resolve_ads_channel(ADC["battery_channel"])
            light_ch = self._resolve_ads_channel(ADC["light_channel"])

            shunt_diff = AnalogIn(ads, shunt_p, shunt_n)
            battery_chan = AnalogIn(ads, battery_ch)
            light_chan = AnalogIn(ads, light_ch)

            initial_batt_adc = battery_chan.voltage
            initial_batt_voltage = self.battery_voltage_from_adc(initial_batt_adc)
            initial_soc_voltage = self.soc_from_voltage(initial_batt_voltage)

            self._ads = ads
            self._shunt_diff = shunt_diff
            self._battery_chan = battery_chan
            self._light_chan = light_chan
            self._hardware_ready = True

            self._battery_voltage_samples.clear()
            self._current_samples.clear()
            self._light_samples.clear()
            self._light_voltage_samples.clear()
            self._battery_voltage_samples.append(initial_batt_voltage)
            self._light_samples.append(self.light_percent_from_voltage(light_chan.voltage))
            self._light_voltage_samples.append(float(light_chan.voltage))
            self._current_samples.append(0.0)

            capacity = float(ADC["battery_capacity_ah"])
            self._remaining_ah = capacity * (initial_soc_voltage / 100.0)
            self._ah_used = capacity - self._remaining_ah
            self._last_time_monotonic = now
            self._next_sample_due_monotonic = now

            if self._last_init_error is not None:
                LOG.info("Telemetry recovered after previous failure")
            self._last_init_error = None
            LOG.info(
                "Telemetry initialised: battery=%.2f V, startup_soc=%.1f%%, ADS addr=%s",
                initial_batt_voltage,
                initial_soc_voltage,
                ADC["i2c_address"],
            )
        except Exception as exc:
            self._hardware_ready = False
            self._ads = None
            self._shunt_diff = None
            self._battery_chan = None
            self._light_chan = None
            msg = str(exc)
            if msg != self._last_init_error:
                LOG.warning("Telemetry unavailable, GPIO control continues: %s", msg)
                self._last_init_error = msg

    def tick(self) -> None:
        if not self._enabled:
            return
        if not self._hardware_ready:
            self._try_initialise()
            return

        now = time.monotonic()
        if now < self._next_sample_due_monotonic:
            return

        self._next_sample_due_monotonic = now + self._sample_interval_sec
        dt = now - self._last_time_monotonic
        self._last_time_monotonic = now

        try:
            v_shunt = float(self._shunt_diff.voltage)
            v_batt_adc = float(self._battery_chan.voltage)
            v_light = float(self._light_chan.voltage)

            current_a = self.shunt_current_from_voltage(v_shunt)
            battery_v = self.battery_voltage_from_adc(v_batt_adc)
            light_pct = self.light_percent_from_voltage(v_light)

            if bool(ADC["discharge_only"]) and current_a < 0 and abs(current_a) < float(ADC["negative_current_noise_floor_a"]):
                current_a = 0.0

            self._battery_voltage_samples.append(battery_v)
            self._current_samples.append(current_a)
            self._light_samples.append(light_pct)
            self._light_voltage_samples.append(v_light)

            avg_battery_v = sum(self._battery_voltage_samples) / len(self._battery_voltage_samples)
            avg_current_a = sum(self._current_samples) / len(self._current_samples)
            avg_light_pct = sum(self._light_samples) / len(self._light_samples)

            if bool(ADC["discharge_only"]):
                if avg_current_a > 0:
                    self._ah_used += avg_current_a * dt / 3600.0
            else:
                self._ah_used += avg_current_a * dt / 3600.0

            capacity_ah = float(ADC["battery_capacity_ah"])
            self._remaining_ah = self._clamp(capacity_ah - self._ah_used, 0.0, capacity_ah)
            soc_coulomb = 100.0 * self._remaining_ah / capacity_ah
            soc_voltage = self.soc_from_voltage(avg_battery_v)
            soc_blended = (float(ADC["soc_coulomb_weight"]) * soc_coulomb) + (float(ADC["soc_voltage_weight"]) * soc_voltage)
            soc_blended = self._clamp(soc_blended, 0.0, 100.0)

            power_w = avg_battery_v * avg_current_a
            values = {
                "battery_voltage": f"{avg_battery_v:.3f}",
                "current_a": f"{avg_current_a:.3f}",
                "power_w": f"{power_w:.3f}",
                "ah_used": f"{self._ah_used:.4f}",
                "soc": f"{soc_blended:.1f}",
                "ambient_light_voltage": f"{self.latest_light_voltage():.3f}",
                "ambient_light_pct": f"{avg_light_pct:.1f}",
            }

            for key, payload in values.items():
                if self._last_published.get(key) != payload:
                    self._publish(f"{self._topic_root()}/{key}", payload)
                    self._last_published[key] = payload
        except Exception as exc:
            LOG.warning("Telemetry read failed, will retry: %s", exc)
            self._hardware_ready = False
            self._ads = None
            self._shunt_diff = None
            self._battery_chan = None
            self._light_chan = None
            self._last_init_error = str(exc)

    def stop(self) -> None:
        self._hardware_ready = False


class GPIOControlService:
    def __init__(self) -> None:
        self._stop = False
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT["client_id"])
        self._client.username_pw_set(MQTT["username"], MQTT["password"])
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._circuits: Dict[str, CircuitRuntime] = {}
        self._sense_inputs: Dict[str, SenseInputRuntime] = {}
        self._drs: Optional[DRSRuntime] = None
        self._drs_lock = threading.RLock()
        self._telemetry = TelemetryMonitor(self._client, self._publish)
        self._configure_gpio()
        self._configure_sense_inputs()
        self._configure_drs()

    @staticmethod
    def _servo_duty(pulse_us: int, frequency_hz: int) -> float:
        return pulse_us * frequency_hz / 1_000_000

    def _configure_drs(self) -> None:
        if not DRS.get("enabled", False):
            return
        self._drs = DRSRuntime(int(DRS["pin"]), int(DRS["frequency_hz"]), int(DRS["close_pulse_us"]),
                               int(DRS["open_pulse_us"]))
        self._set_drs("CLOSE", publish=False)

    def _set_drs(self, state: str, publish: bool = True) -> None:
        with self._drs_lock:
            if self._drs is None:
                return
            if self._drs.output is None:
                self._drs.output = PWMOutputDevice(self._drs.pin, frequency=self._drs.frequency_hz, initial_value=0)
            target_pulse = self._drs.open_pulse_us if state == "OPEN" else self._drs.close_pulse_us
            start_pulse = self._drs.current_pulse_us
            transition_seconds = float(DRS.get("transition_seconds", 0))
            if start_pulse is None or transition_seconds <= 0:
                self._drs.output.value = self._servo_duty(target_pulse, self._drs.frequency_hz)
            else:
                steps = max(1, round(transition_seconds / 0.01))
                for step in range(1, steps + 1):
                    pulse = start_pulse + ((target_pulse - start_pulse) * step / steps)
                    self._drs.output.value = self._servo_duty(pulse, self._drs.frequency_hz)
                    if step != steps:
                        time.sleep(transition_seconds / steps)
            self._drs.current_pulse_us = target_pulse
            self._drs.state = state
            if DRS.get("switch_behavior") == "pulse_release":
                self._drs.pulse_release_at_monotonic = time.monotonic() + float(DRS["pulse_release_seconds"])
            else:
                self._drs.pulse_release_at_monotonic = None
            if publish:
                self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/state", state)

    def _release_drs_if_due(self) -> None:
        with self._drs_lock:
            if self._drs is None or self._drs.output is None or self._drs.pulse_release_at_monotonic is None:
                return
            if time.monotonic() < self._drs.pulse_release_at_monotonic:
                return
            self._drs.output.close()
            self._drs.output = None
            self._drs.pulse_release_at_monotonic = None
            LOG.info("DRS PWM released after movement to %s", self._drs.state)

    @staticmethod
    def _is_horn(runtime: CircuitRuntime) -> bool:
        return runtime.config.name == "horn"

    def _handle_remote_pulse_expiry(self) -> None:
        now = time.monotonic()
        for runtime in self._circuits.values():
            if not self._is_horn(runtime):
                continue
            if runtime.remote_pulse_until_monotonic is None:
                continue
            if now >= runtime.remote_pulse_until_monotonic:
                runtime.remote_pulse_until_monotonic = None
                if runtime.requested_state != STATE_OFF:
                    runtime.requested_state = STATE_OFF
                    self._apply_logic(runtime, publish=True)

    def _configure_gpio(self) -> None:
        for trike_name, trike_cfg in TRIKES.items():
            if not trike_cfg.get("enabled", False):
                continue
            for circuit_name, cfg in trike_cfg["circuits"].items():
                if not cfg.get("enabled", False):
                    continue

                switch_mode = str(cfg.get("switch_mode", MODE_BINARY)).strip().lower()
                if switch_mode not in (MODE_BINARY, MODE_THREE_POSITION):
                    raise ValueError(f"Unsupported switch_mode for {trike_name}/{circuit_name}: {switch_mode}")

                circuit = CircuitConfig(
                    trike=trike_name,
                    name=circuit_name,
                    friendly_name=cfg["friendly_name"],
                    icon=cfg["icon"],
                    output_pin=int(cfg["output_pin"]),
                    boot_default=cfg["boot_default"],
                    output_active_high=bool(cfg["output_active_high"]),
                    switch_mode=switch_mode,
                    switch_pin=cfg.get("switch_pin"),
                    switch_pull_up=cfg.get("switch_pull_up", True),
                    switch_active_high=bool(cfg.get("switch_active_high", False)),
                    switch_pin_on=cfg.get("switch_pin_on"),
                    switch_pin_auto=cfg.get("switch_pin_auto"),
                    three_position_pull_up=cfg.get("three_position_pull_up", True),
                    three_position_active_high=bool(cfg.get("three_position_active_high", False)),
                    auto_enabled=bool(cfg.get("auto_enabled", False)),
                    auto_on_below_pct=cfg.get("auto_on_below_pct"),
                    auto_off_above_pct=cfg.get("auto_off_above_pct"),
                )

                output = OutputDevice(
                    pin=circuit.output_pin,
                    active_high=circuit.output_active_high,
                    initial_value=(circuit.boot_default == STATE_ON),
                )

                switch = None
                switch_on = None
                switch_auto = None

                if circuit.switch_mode == MODE_BINARY:
                    if circuit.switch_pin is not None:
                        switch = Button(
                            pin=int(circuit.switch_pin),
                            pull_up=circuit.switch_pull_up,
                            bounce_time=0.05,
                        )
                else:
                    if circuit.switch_pin_on is None or circuit.switch_pin_auto is None:
                        LOG.warning(
                            "%s/%s is configured for three_position mode but switch_pin_on or switch_pin_auto is unset.",
                            trike_name,
                            circuit_name,
                        )
                    if circuit.switch_pin_on is not None:
                        switch_on = self._make_button(
                            pin=int(circuit.switch_pin_on),
                            pull_up=circuit.three_position_pull_up,
                            active_high=circuit.three_position_active_high,
                        )
                    if circuit.switch_pin_auto is not None:
                        switch_auto = self._make_button(
                            pin=int(circuit.switch_pin_auto),
                            pull_up=circuit.three_position_pull_up,
                            active_high=circuit.three_position_active_high,
                        )

                circuit_key = self._key(circuit.trike, circuit.name)
                runtime = CircuitRuntime(
                    config=circuit,
                    output=output,
                    switch=switch,
                    switch_on=switch_on,
                    switch_auto=switch_auto,
                    requested_state=circuit.boot_default,
                    effective_state=circuit.boot_default,
                    source=SOURCE_REMOTE,
                )
                self._circuits[circuit_key] = runtime

                if switch is not None:
                    switch.when_pressed = lambda ck=circuit_key: self._on_switch_event(ck)
                    switch.when_released = lambda ck=circuit_key: self._on_switch_event(ck)
                if switch_on is not None:
                    switch_on.when_pressed = lambda ck=circuit_key: self._on_switch_event(ck)
                    switch_on.when_released = lambda ck=circuit_key: self._on_switch_event(ck)
                if switch_auto is not None:
                    switch_auto.when_pressed = lambda ck=circuit_key: self._on_switch_event(ck)
                    switch_auto.when_released = lambda ck=circuit_key: self._on_switch_event(ck)

                self._apply_logic(runtime, publish=False)

    def _configure_sense_inputs(self) -> None:
        for trike_name, inputs_cfg in INPUTS.items():
            for name, cfg in inputs_cfg.items():
                if not cfg.get("enabled", False):
                    continue
                si = SenseInputConfig(
                    trike=trike_name,
                    name=name,
                    friendly_name=cfg["friendly_name"],
                    icon=cfg["icon"],
                    pin=int(cfg["pin"]),
                    pull_up=cfg.get("pull_up", True),
                    active_high=bool(cfg.get("active_high", False)),
                    state_semantics=cfg.get("state_semantics", "legacy"),
                )
                button = self._make_button(pin=si.pin, pull_up=si.pull_up, active_high=si.active_high)
                key = self._key(si.trike, si.name)
                runtime = SenseInputRuntime(config=si, button=button)
                self._sense_inputs[key] = runtime
                button.when_pressed = lambda k=key: self._publish_sense_input(self._sense_inputs[k])
                button.when_released = lambda k=key: self._publish_sense_input(self._sense_inputs[k])

    @staticmethod
    def _key(trike: str, name: str) -> str:
        return f"{trike}:{name}"

    @staticmethod
    def _base_topic(trike: str, circuit: str) -> str:
        return f"{MQTT['topic_root']}/{trike}/control/{circuit}"

    @staticmethod
    def _switch_topic(trike: str, circuit: str) -> str:
        return f"{MQTT['topic_root']}/{trike}/input/{circuit}_switch/state"

    @staticmethod
    def _sense_topic(trike: str, name: str) -> str:
        return f"{MQTT['topic_root']}/{trike}/input/{name}/state"

    @staticmethod
    def _sanitize_payload(payload: bytes) -> str:
        return payload.decode("utf-8", errors="ignore").strip().upper()

    def _publish(self, topic: str, payload: str, retain: Optional[bool] = None) -> None:
        if retain is None:
            retain = MQTT["retain_state"]
        self._client.publish(topic, payload, qos=MQTT["qos"], retain=retain)
        LOG.debug("Published %s => %s", topic, payload)

    @staticmethod
    def _make_button(pin: int, pull_up: Optional[bool], active_high: bool) -> Button:
        """Create a gpiozero Button for both pulled and floating inputs.

        gpiozero requires active_state when pull_up=None. For floating inputs,
        is_pressed should already represent the configured active level.
        """
        kwargs = {"pin": pin, "pull_up": pull_up, "bounce_time": 0.05}
        if pull_up is None:
            kwargs["active_state"] = bool(active_high)
        return Button(**kwargs)

    def _binary_switch_forced_on(self, runtime: CircuitRuntime) -> bool:
        if runtime.switch is None:
            return False
        raw_pressed = runtime.switch.is_pressed
        if runtime.config.switch_pull_up is None:
            return bool(raw_pressed)
        return bool(raw_pressed) if runtime.config.switch_active_high else not bool(raw_pressed)

    def _three_position_active(self, button: Optional[Button], active_high: bool, pull_up: Optional[bool]) -> bool:
        if button is None:
            return False
        raw_pressed = button.is_pressed
        if pull_up is None:
            return bool(raw_pressed)
        return bool(raw_pressed) if active_high else not bool(raw_pressed)

    def _three_position_state(self, runtime: CircuitRuntime) -> str:
        on_active = self._three_position_active(
            runtime.switch_on, runtime.config.three_position_active_high, runtime.config.three_position_pull_up
        )
        auto_active = self._three_position_active(
            runtime.switch_auto, runtime.config.three_position_active_high, runtime.config.three_position_pull_up
        )

        if on_active and auto_active:
            LOG.warning(
                "Invalid three-position switch state for %s/%s: ON and AUTO both active; forcing OFF",
                runtime.config.trike,
                runtime.config.name,
            )
            return POSITION_OFF
        if on_active:
            return POSITION_ON
        if auto_active:
            return POSITION_AUTO
        return POSITION_OFF

    def _auto_effective_state(self, runtime: CircuitRuntime) -> str:
        if not runtime.config.auto_enabled:
            return STATE_OFF
        light_pct = self._telemetry.latest_light_percent()
        if light_pct is None:
            return STATE_OFF

        on_below = runtime.config.auto_on_below_pct
        off_above = runtime.config.auto_off_above_pct
        if on_below is None or off_above is None or on_below >= off_above:
            LOG.warning("AUTO thresholds invalid or missing for %s/%s; forcing OFF", runtime.config.trike, runtime.config.name)
            return STATE_OFF

        if light_pct <= on_below:
            runtime.auto_latched_on = True
        elif light_pct >= off_above:
            runtime.auto_latched_on = False

        return STATE_ON if runtime.auto_latched_on else STATE_OFF

    def _ambient_auto_enabled(self, runtime: CircuitRuntime) -> bool:
        return bool(AMBIENT_AUTO.get("enabled", False)) and runtime.config.name == "headlight"

    def _ambient_auto_state(self, runtime: CircuitRuntime) -> str:
        voltage = self._telemetry.latest_light_voltage()
        if voltage is None:
            return runtime.effective_state
        on_below = float(AMBIENT_AUTO["on_below_voltage"])
        off_above = float(AMBIENT_AUTO["off_above_voltage"])
        desired = STATE_ON if voltage <= on_below else STATE_OFF if voltage >= off_above else runtime.effective_state
        if desired == runtime.effective_state:
            runtime.ambient_candidate_state = None
            runtime.ambient_candidate_since_monotonic = None
            return desired
        now = time.monotonic()
        if runtime.ambient_candidate_state != desired:
            runtime.ambient_candidate_state = desired
            runtime.ambient_candidate_since_monotonic = now
            return runtime.effective_state
        debounce = float(AMBIENT_AUTO.get("debounce_seconds", 0))
        if now - (runtime.ambient_candidate_since_monotonic or now) >= debounce:
            runtime.ambient_candidate_state = None
            runtime.ambient_candidate_since_monotonic = None
            return desired
        return runtime.effective_state

    def _switch_state_payload(self, runtime: CircuitRuntime) -> str:
        if runtime.config.switch_mode == MODE_BINARY:
            return STATE_ON if self._binary_switch_forced_on(runtime) else STATE_OFF
        return self._three_position_state(runtime)

    def _sense_input_state(self, runtime: SenseInputRuntime) -> str:
        raw = runtime.button.is_pressed
        if runtime.config.state_semantics == "electrical":
            # Button already converts a pulled-up input to active-low semantics.
            active = bool(raw)
            if runtime.config.pull_up is not None:
                button_active_high = not runtime.config.pull_up
                if runtime.config.active_high != button_active_high:
                    active = not active
        elif runtime.config.pull_up is None:
            active = bool(raw)
        else:
            active = bool(raw) if runtime.config.active_high else not bool(raw)
        return STATE_ON if active else STATE_OFF

    def _apply_logic(self, runtime: CircuitRuntime, publish: bool = True) -> None:
        if runtime.config.switch_mode == MODE_BINARY:
            if self._ambient_auto_enabled(runtime):
                effective = self._ambient_auto_state(runtime)
                source = SOURCE_AUTO
            else:
                local_forced_on = self._binary_switch_forced_on(runtime)
                if runtime.switch is None:
                    effective = runtime.requested_state
                    source = SOURCE_REMOTE
                elif local_forced_on:
                    effective = STATE_ON
                    source = SOURCE_LOCAL
                else:
                    effective = runtime.requested_state
                    source = SOURCE_REMOTE
        else:
            switch_pos = self._three_position_state(runtime)
            if switch_pos == POSITION_ON:
                effective = STATE_ON
                source = SOURCE_LOCAL
            elif switch_pos == POSITION_AUTO:
                effective = self._auto_effective_state(runtime)
                source = SOURCE_AUTO
            else:
                effective = STATE_OFF
                source = SOURCE_LOCAL

        if effective == STATE_ON:
            runtime.output.on()
        else:
            runtime.output.off()

        runtime.effective_state = effective
        runtime.source = source
        if publish:
            self._publish_runtime(runtime)

    def _publish_runtime(self, runtime: CircuitRuntime) -> None:
        trike = runtime.config.trike
        name = runtime.config.name
        base = self._base_topic(trike, name)
        self._publish(f"{base}/requested_state", runtime.requested_state)
        self._publish(f"{base}/state", runtime.effective_state)
        self._publish(f"{base}/source", runtime.source)
        switch_state = self._switch_state_payload(runtime)
        self._publish(self._switch_topic(trike, name), switch_state)
        runtime.last_switch_state = switch_state

    def _publish_sense_input(self, runtime: SenseInputRuntime) -> None:
        payload = self._sense_input_state(runtime)
        self._publish(self._sense_topic(runtime.config.trike, runtime.config.name), payload)
        runtime.last_state = payload

    def _publish_all_states(self) -> None:
        for runtime in self._circuits.values():
            self._publish_runtime(runtime)
        for runtime in self._sense_inputs.values():
            self._publish_sense_input(runtime)
        if self._drs is not None:
            self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/state", self._drs.state)

    def _discovery_topic(self, component: str, object_id: str) -> str:
        return f"{MQTT['discovery_prefix']}/{component}/{object_id}/config"

    def _device_block(self, trike: str) -> dict:
        return {
            "identifiers": [f"{trike}_control"],
            "name": f"{trike.upper()} Control",
            "manufacturer": "HPR",
            "model": "Control Module",
        }

    def _publish_discovery(self) -> None:
        if not SERVICE["discovery_enabled"]:
            return

        for runtime in self._circuits.values():
            trike = runtime.config.trike
            name = runtime.config.name
            friendly = runtime.config.friendly_name
            icon = runtime.config.icon
            base = self._base_topic(trike, name)
            device = self._device_block(trike)

            switch_object_id = f"{trike}_control_{name}"
            switch_payload = {
                "name": friendly,
                "object_id": switch_object_id,
                "unique_id": switch_object_id,
                "icon": icon,
                "command_topic": f"{base}/set",
                "state_topic": f"{base}/state",
                "payload_on": STATE_ON,
                "payload_off": STATE_OFF,
                "state_on": STATE_ON,
                "state_off": STATE_OFF,
                "device": device,
            }
            self._publish(self._discovery_topic("switch", switch_object_id), json.dumps(switch_payload), retain=MQTT["retain_discovery"])

            input_object_id = f"{trike}_control_{name}_switch"
            input_payload = {
                "name": f"{friendly} Switch",
                "object_id": input_object_id,
                "unique_id": input_object_id,
                "icon": "mdi:toggle-switch",
                "state_topic": self._switch_topic(trike, name),
                "payload_on": STATE_ON,
                "payload_off": STATE_OFF,
                "device": device,
            }
            self._publish(self._discovery_topic("binary_sensor", input_object_id), json.dumps(input_payload), retain=MQTT["retain_discovery"])

            source_object_id = f"{trike}_control_{name}_source"
            source_payload = {
                "name": f"{friendly} Source",
                "object_id": source_object_id,
                "unique_id": source_object_id,
                "icon": "mdi:source-branch",
                "state_topic": f"{base}/source",
                "device": device,
            }
            self._publish(self._discovery_topic("sensor", source_object_id), json.dumps(source_payload), retain=MQTT["retain_discovery"])

        for runtime in self._sense_inputs.values():
            trike = runtime.config.trike
            name = runtime.config.name
            device = self._device_block(trike)
            object_id = f"{trike}_input_{name}"
            payload = {
                "name": runtime.config.friendly_name,
                "object_id": object_id,
                "unique_id": object_id,
                "icon": runtime.config.icon,
                "state_topic": self._sense_topic(trike, name),
                "payload_on": STATE_ON,
                "payload_off": STATE_OFF,
                "device": device,
            }
            self._publish(self._discovery_topic("binary_sensor", object_id), json.dumps(payload), retain=MQTT["retain_discovery"])

        self._telemetry._publish_discovery()

    def _on_switch_event(self, circuit_key: str) -> None:
        runtime = self._circuits[circuit_key]
        self._apply_logic(runtime, publish=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        LOG.info("MQTT connected: rc=%s", reason_code)
        for runtime in self._circuits.values():
            base = self._base_topic(runtime.config.trike, runtime.config.name)
            client.subscribe(f"{base}/set", qos=MQTT["qos"])
        if self._drs is not None:
            client.subscribe(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/set", qos=MQTT["qos"])
        if SERVICE["publish_discovery_on_start"]:
            self._publish_discovery()
        if SERVICE["republish_state_on_reconnect"]:
            self._publish_all_states()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        LOG.warning("MQTT disconnected: rc=%s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        payload = self._sanitize_payload(msg.payload)
        if self._drs is not None and msg.topic == f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/set":
            state = {"ON": "OPEN", "OFF": "CLOSE"}.get(payload, payload)
            if state not in ("OPEN", "CLOSE"):
                LOG.warning("Ignoring invalid DRS payload on %s: %r", msg.topic, payload)
                return
            self._set_drs(state)
            return
        if payload not in (STATE_ON, STATE_OFF):
            LOG.warning("Ignoring invalid payload on %s: %r", msg.topic, payload)
            return
        for runtime in self._circuits.values():
            base = self._base_topic(runtime.config.trike, runtime.config.name)
            if msg.topic == f"{base}/set":
                if self._is_horn(runtime):
                    if payload == STATE_ON:
                        runtime.requested_state = STATE_ON
                        runtime.remote_pulse_until_monotonic = time.monotonic() + HORN_REMOTE_PULSE_SECONDS
                    else:
                        runtime.requested_state = STATE_OFF
                        runtime.remote_pulse_until_monotonic = None
                else:
                    runtime.requested_state = payload
                self._apply_logic(runtime, publish=True)
                return
        LOG.debug("No circuit matched MQTT topic %s", msg.topic)

    def start(self) -> int:
        self._client.connect_async(MQTT["host"], MQTT["port"], keepalive=60)
        self._client.loop_start()
        self._publish_all_states()
        self._telemetry.start()
        try:
            while not self._stop:
                self._telemetry.tick()
                self._handle_remote_pulse_expiry()
                self._release_drs_if_due()
                for runtime in self._circuits.values():
                    if self._ambient_auto_enabled(runtime):
                        self._apply_logic(runtime, publish=True)
                    elif runtime.config.switch_mode == MODE_THREE_POSITION and runtime.config.auto_enabled:
                        if self._three_position_state(runtime) == POSITION_AUTO:
                            self._apply_logic(runtime, publish=True)
                time.sleep(float(SERVICE["poll_interval_seconds"]))
        finally:
            self._telemetry.stop()
            self._client.loop_stop()
            self._client.disconnect()
            for runtime in self._sense_inputs.values():
                runtime.button.close()
            for runtime in self._circuits.values():
                if runtime.switch is not None:
                    runtime.switch.close()
                if runtime.switch_on is not None:
                    runtime.switch_on.close()
                if runtime.switch_auto is not None:
                    runtime.switch_auto.close()
                runtime.output.close()
            if self._drs is not None and self._drs.output is not None:
                self._drs.output.close()
        return 0

    def stop(self) -> None:
        self._stop = True


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, SERVICE["log_level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    parser = config_arg_parser("Run HPR GPIO control service")
    args = parser.parse_args()
    config = load_config(args.config)
    if not config.enabled("gpio_control"):
        return 0
    configure(config)
    _setup_logging()
    service = GPIOControlService()

    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if notify_socket:
        address = "\0" + notify_socket[1:] if notify_socket.startswith("@") else notify_socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(b"READY=1")

    def _handle_signal(signum, frame) -> None:
        LOG.info("Signal received: %s", signum)
        service.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return service.start()


if __name__ == "__main__":
    sys.exit(main())

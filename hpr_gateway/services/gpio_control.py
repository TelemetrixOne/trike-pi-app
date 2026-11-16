#!/usr/bin/env python3
"""HPR GPIO control service.

Features
- Reads local physical switch inputs.
- Drives output pins for trike circuits.
- Publishes native MQTT control, state and health topics.
- Supports ADS1115 telemetry for ambient light, battery voltage, current,
  power, Ah used, and SOC.
- Publishes additional engineer-provided sense inputs as native MQTT states.
- Keeps GPIO control alive if telemetry hardware or libraries are unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

import paho.mqtt.client as mqtt
from gpiozero import InputDevice, OutputDevice, PWMOutputDevice

from hpr_gateway.config import HprConfig, config_arg_parser, enabled_items, load_config
from hpr_gateway.drs_protocol import DRSClock, DRSProtocol
from hpr_gateway.credential_reload import CredentialReload

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
HEADLIGHT_MODE = "headlight_mode"

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
        "topic_root": config.topic_root,
        "gpio_evidence_topic": f"{config.resolve_topic(config.get('health.base_topic'), 'pi')}/gpio_evidence",
        "qos": int(service.get("qos", 1)),
        "retain_state": bool(service.get("retain_state", True)),
    }
    SERVICE = {
        "republish_state_on_reconnect": bool(service.get("republish_state_on_reconnect", True)),
        "poll_interval_seconds": float(service.get("poll_interval_seconds", 0.02)),
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
        "battery_empty_soc_pct": float(adc.get("battery_empty_soc_pct", 0.0)),
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
            "control_type": str(item.get("control_type", "binary")),
            "output_pin": int(item.get("output_pin", item.get("pin"))),
            "boot_default": str(item.get("boot_default", "OFF")),
            "output_active_high": bool(item.get("output_active_high", True)),
            "icon": str(item.get("icon", "")),
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
            "icon": str(item.get("icon", "")),
        }
    INPUTS = {config.trike_id: inputs}
    drs = (gpio.get("pwm_outputs", {}) or {}).get("drs", {}) or {}
    drs_control = (gpio.get("topic_controls", {}) or {}).get("drs", {}) or {}
    boot_mode = str(drs_control.get("boot_default", "CLOSED")).upper()
    if boot_mode == "CLOSE":
        boot_mode = "CLOSED"
    if boot_mode not in ("OPEN", "CLOSED", "RIDER_CONTROL"):
        boot_mode = "CLOSED"
    DRS = {"enabled": bool(drs.get("enabled", False)), "trike": config.trike_id,
           "pin": drs.get("pin"), "frequency_hz": drs.get("frequency_hz"),
           "close_pulse_us": drs.get("close_pulse_us"), "open_pulse_us": drs.get("open_pulse_us"),
           "switch_behavior": drs.get("switch_behavior", "remote_open_close"),
           "transition_seconds": drs.get("transition_seconds", 0),
           "pulse_release_seconds": drs.get("pulse_release_seconds"),
           "boot_mode": boot_mode,
           "rider_control_input": str(drs_control.get("rider_control_input", "drs_switch"))}
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
    control_type: str = "binary"

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


class PolledInput(InputDevice):
    """Sample and debounce inputs without requesting kernel edge interrupts."""

    def __init__(self, *args, debounce_seconds=0.05, **kwargs):
        super().__init__(*args, **kwargs)
        self._stable_pressed = bool(self.is_active)
        self._candidate_pressed = self._stable_pressed
        self._candidate_since = time.monotonic()
        self._debounce_seconds = debounce_seconds

    @property
    def is_pressed(self):
        pressed = bool(self.is_active)
        now = time.monotonic()
        if pressed != self._candidate_pressed:
            self._candidate_pressed = pressed
            self._candidate_since = now
        elif now - self._candidate_since >= self._debounce_seconds:
            self._stable_pressed = pressed
        return self._stable_pressed


@dataclass
class CircuitRuntime:
    config: CircuitConfig
    output: OutputDevice
    switch: Optional[PolledInput]
    switch_on: Optional[PolledInput]
    switch_auto: Optional[PolledInput]
    requested_state: str
    effective_state: str
    source: str
    mode: Optional[str] = None
    remote_pulse_until_monotonic: Optional[float] = None
    ambient_candidate_state: Optional[str] = None
    ambient_candidate_since_monotonic: Optional[float] = None
    last_switch_state: Optional[str] = None
    auto_latched_on: bool = False


@dataclass
class DRSRuntime:
    pin: int
    frequency_hz: int
    close_pulse_us: int
    open_pulse_us: int
    output: Optional[PWMOutputDevice] = None
    state: str = "CLOSE"
    mode: str = "CLOSED"
    source: str = SOURCE_REMOTE
    current_pulse_us: Optional[float] = None
    last_switch_state: Optional[str] = None
    auto_latched_on: bool = False
    pulse_release_at_monotonic: Optional[float] = None
    transition_start_pulse_us: Optional[float] = None
    transition_target_pulse_us: Optional[float] = None
    transition_started_monotonic: Optional[float] = None
    last_actuation_monotonic: Optional[float] = None
    error: Optional[str] = None


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
    button: PolledInput
    last_state: Optional[str] = None


class BoundedConversionMixin:
    """Bound the ADS driver readiness loop and yield between status reads."""

    def _read(self, pin):
        self._conversion_deadline = time.monotonic() + 0.1
        return super()._read(pin)

    def _conversion_complete(self):
        complete = super()._conversion_complete()
        if not complete:
            if time.monotonic() >= self._conversion_deadline:
                raise TimeoutError("ADS1115 conversion exceeded 100 ms")
            time.sleep(0.002)
        return complete


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
        self._i2c = None
        self._worker = None
        self._stopping = threading.Event()
        self._last_sample_monotonic = None

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

    def calibrated_soc(self, raw_soc: float, battery_voltage: float) -> float:
        """Map an observed flat-battery SOC reading to zero."""
        empty_soc = float(ADC["battery_empty_soc_pct"])
        empty_voltage = float(ADC["battery_empty_voltage"])
        if not 0.0 <= empty_soc < 100.0:
            raise ValueError("battery_empty_soc_pct must be from 0 to less than 100")
        if battery_voltage <= empty_voltage:
            return 0.0
        return self._clamp(100.0 * (raw_soc - empty_soc) / (100.0 - empty_soc), 0.0, 100.0)

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
        if not self._light_samples or not self._sample_fresh():
            return None
        return sum(self._light_samples) / len(self._light_samples)

    def latest_light_voltage(self) -> Optional[float]:
        if not self._light_voltage_samples or not self._sample_fresh():
            return None
        return sum(self._light_voltage_samples) / len(self._light_voltage_samples)

    def _sample_fresh(self) -> bool:
        return (self._hardware_ready and self._last_sample_monotonic is not None and
                time.monotonic() - self._last_sample_monotonic <= max(3.0, self._sample_interval_sec * 3))

    def publish_latest(self) -> None:
        if self._sample_fresh():
            for key, payload in dict(self._last_published).items():
                self._publish(f"{self._topic_root()}/{key}", payload)

    def health_snapshot(self) -> dict:
        age = (time.monotonic() - self._last_sample_monotonic
               if self._last_sample_monotonic is not None else None)
        return {"state": "disabled" if not self._enabled else "online" if self._sample_fresh() else "stale",
                "sample_age_seconds": round(age, 2) if age is not None else None,
                "error": self._last_init_error}

    def start(self) -> None:
        if not self._enabled:
            LOG.info("Telemetry disabled in config")
            return
        self._worker = threading.Thread(target=self._run, name="gpio-adc", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        try:
            self._try_initialise(force=True)
            while not self._stopping.is_set():
                self.tick()
                self._stopping.wait(0.05)
        finally:
            self._close_hardware()

    def _close_hardware(self) -> None:
        if self._i2c is not None:
            try:
                self._i2c.deinit()
            except Exception:
                LOG.debug("ADC I2C cleanup failed", exc_info=True)
            self._i2c = None

    def _try_initialise(self, force: bool = False) -> None:
        if not self._enabled:
            return

        now = time.monotonic()
        if not force and (now - self._last_attempt_monotonic) < self._retry_interval_sec:
            return
        self._last_attempt_monotonic = now
        self._close_hardware()

        try:
            import board  # type: ignore
            import busio  # type: ignore
            import adafruit_ads1x15.ads1115 as ADS  # type: ignore
            from adafruit_ads1x15.analog_in import AnalogIn  # type: ignore

            class BoundedADS1115(BoundedConversionMixin, ADS.ADS1115):
                pass

            i2c = busio.I2C(board.SCL, board.SDA)
            self._i2c = i2c
            ads = BoundedADS1115(
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
            self._last_sample_monotonic = time.monotonic()

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
            self._last_sample_monotonic = time.monotonic()

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
            raw_soc = (float(ADC["soc_coulomb_weight"]) * soc_coulomb) + (float(ADC["soc_voltage_weight"]) * soc_voltage)
            soc_blended = self.calibrated_soc(raw_soc, avg_battery_v)

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
        self._stopping.set()
        if self._worker is not None:
            self._worker.join(timeout=1)
        self._hardware_ready = False


class GPIOControlService:
    def __init__(self) -> None:
        self._stop = False
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT["client_id"])
        self._client.username_pw_set(MQTT["username"], MQTT["password"])
        self._client.max_queued_messages_set(128)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._circuits: Dict[str, CircuitRuntime] = {}
        self._sense_inputs: Dict[str, SenseInputRuntime] = {}
        self._drs: Optional[DRSRuntime] = None
        self._drs_lock = threading.RLock()
        self._mqtt_connected = False
        self._events = queue.Queue(maxsize=64)
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
        self._drs_protocol = DRSProtocol()
        self._drs_clock = DRSClock()
        self._drs = DRSRuntime(int(DRS["pin"]), int(DRS["frequency_hz"]), int(DRS["close_pulse_us"]),
                               int(DRS["open_pulse_us"]))
        self._set_drs_mode(DRS["boot_mode"], publish=False)

    def _set_drs_mode(self, mode: str, publish: bool = True, retry: bool = False) -> bool:
        if self._drs is None:
            return
        with self._drs_lock:
            self._drs.mode = mode
            if mode == "RIDER_CONTROL":
                result = (self._apply_drs_rider_switch(publish=publish, retry=True) if retry
                          else self._apply_drs_rider_switch(publish=publish))
            else:
                self._drs.source = SOURCE_REMOTE
                result = (self._set_drs("OPEN" if mode == "OPEN" else "CLOSE", publish=publish, force=True)
                          if retry else self._set_drs("OPEN" if mode == "OPEN" else "CLOSE", publish=publish))
            if result is False:
                return False
            if publish:
                self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/mode", mode)
                self._publish_drs_status()
            return True

    def _apply_drs_rider_switch(self, publish: bool = True, retry: bool = False) -> bool:
        if self._drs is None:
            return
        input_runtime = self._sense_inputs.get(self._key(DRS["trike"], DRS["rider_control_input"]))
        switch_on = input_runtime is not None and self._sense_input_state(input_runtime) == STATE_ON
        self._drs.source = SOURCE_LOCAL
        return (self._set_drs("OPEN" if switch_on else "CLOSE", publish=publish, force=True)
                if retry else self._set_drs("OPEN" if switch_on else "CLOSE", publish=publish))

    def _set_drs(self, state: str, publish: bool = True, force: bool = False) -> bool:
        try:
            self._drive_drs(state, publish=publish, force=force)
            return True
        except Exception as exc:
            self._drs_failed(exc)
            return False

    def _drs_failed(self, exc) -> None:
        self._drs.error = type(exc).__name__ + ': ' + str(exc)[:160]
        self._drs.transition_target_pulse_us = None
        self._drs.pulse_release_at_monotonic = None
        if self._drs.output is not None:
            try:
                self._drs.output.close()
            except Exception:
                pass
        self._drs.output = None
        LOG.error('DRS actuator error (position unverified): %s', self._drs.error)
        self._publish_drs_status(force=True)

    def _drive_drs(self, state: str, publish: bool = True, force: bool = False) -> None:
        with self._drs_lock:
            if self._drs is None:
                return
            target_pulse = self._drs.open_pulse_us if state == "OPEN" else self._drs.close_pulse_us
            duplicate = not force and not self._drs.error and self._drs.state == state and (
                self._drs.current_pulse_us == target_pulse or
                self._drs.transition_target_pulse_us == target_pulse
            )
            if not duplicate:
                self._drs.error = None
                self._drs.last_actuation_monotonic = time.monotonic()
                if self._drs.output is None:
                    self._drs.output = PWMOutputDevice(self._drs.pin, frequency=self._drs.frequency_hz, initial_value=0)
                start_pulse = self._drs.current_pulse_us
                transition_seconds = float(DRS.get("transition_seconds", 0))
                self._drs.pulse_release_at_monotonic = None
                if start_pulse is None or start_pulse == target_pulse or transition_seconds <= 0:
                    self._drs.output.value = self._servo_duty(target_pulse, self._drs.frequency_hz)
                    self._drs.current_pulse_us = target_pulse
                    self._drs.transition_target_pulse_us = None
                    self._schedule_drs_release()
                else:
                    self._drs.transition_start_pulse_us = start_pulse
                    self._drs.transition_target_pulse_us = target_pulse
                    self._drs.transition_started_monotonic = time.monotonic()
            self._drs.state = state
        if publish:
            self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/state", state)
            self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/source", self._drs.source)
            self._publish_drs_status()

    def _drs_phase(self) -> str:
        if self._drs.error:
            return 'error'
        if self._drs.transition_target_pulse_us is not None:
            return 'moving'
        if self._drs.pulse_release_at_monotonic is not None:
            return 'holding'
        return 'released' if self._drs.output is None and self._drs.current_pulse_us is not None else 'idle'

    def _drs_event(self) -> dict:
        event = self._drs_protocol.event(self._drs.mode, self._drs.state, self._drs_phase())
        event['source'] = self._drs.source
        event['clock_ready'] = bool(getattr(self, '_drs_clock', None) and self._drs_clock.ready)
        if self._drs.error:
            event['error'] = self._drs.error
        return event

    def _publish_drs_status(self, force=False) -> None:
        protocol = getattr(self, '_drs_protocol', None)
        if self._drs is None or protocol is None:
            return
        key = (self._drs.mode, self._drs.state, self._drs.source, self._drs_phase(),
               self._drs.error, protocol.last_command_id,
               bool(getattr(self, '_drs_clock', None) and self._drs_clock.ready))
        now = time.monotonic()
        if not force and key == protocol.last_status and now < protocol.next_status_at:
            return
        self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/status",
                      json.dumps(self._drs_event(), separators=(',', ':')), retain=True, qos=1)
        protocol.last_status, protocol.next_status_at = key, now + 5

    def _drs_ack(self, result) -> None:
        event = self._drs_event()
        event.update(result)
        self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/ack",
                      json.dumps(event, separators=(',', ':')), retain=False, qos=1)
        LOG.info('DRS command id=%s result=%s reason=%s mode=%s target=%s phase=%s',
                 event.get('command_id'), event['result'], event.get('reason', ''),
                 event['mode'], event['target'], event['phase'])

    def _handle_drs_command(self, msg) -> None:
        protocol = self._drs_protocol
        command, rejection = protocol.parse(msg.payload, msg.retain, self._drs_clock.ready)
        if rejection:
            self._drs_ack(rejection)
            return
        mode, ident = command['mode'], command['command_id']
        if mode == 'RIDER_CONTROL':
            sensor = self._sense_inputs.get(self._key(DRS['trike'], DRS['rider_control_input']))
            target = 'OPEN' if sensor is not None and self._sense_input_state(sensor) == STATE_ON else 'CLOSE'
        else:
            target = 'OPEN' if mode == 'OPEN' else 'CLOSE'
        retry = target == self._drs.state and self._drs.output is None
        if target == self._drs.state and self._drs_phase() in ('moving', 'holding'):
            result = {'result': 'busy', 'reason': 'actuation_in_progress', 'command_id': ident}
        elif retry and self._drs.last_actuation_monotonic is not None and time.monotonic() - self._drs.last_actuation_monotonic < 1:
            result = {'result': 'busy', 'reason': 'retry_cooldown', 'command_id': ident}
        else:
            previous = protocol.last_command_id
            protocol.last_command_id = ident
            success = self._set_drs_mode(mode, retry=retry)
            if not success:
                protocol.last_command_id = previous
            result = {'result': 'accepted' if success else 'rejected', 'command_id': ident}
            if not success:
                result['reason'] = 'actuator_error'
        protocol.remember(ident, result['result'])
        self._drs_ack(result)
        self._publish_drs_status(force=True)

    def _schedule_drs_release(self) -> None:
        if DRS.get("switch_behavior") == "pulse_release":
            self._drs.pulse_release_at_monotonic = time.monotonic() + float(DRS["pulse_release_seconds"])

    def _advance_drs(self) -> None:
        if self._drs is None or self._drs.transition_target_pulse_us is None:
            return
        fraction = min(1.0, (time.monotonic() - self._drs.transition_started_monotonic) /
                       float(DRS["transition_seconds"]))
        pulse = self._drs.transition_start_pulse_us + (
            self._drs.transition_target_pulse_us - self._drs.transition_start_pulse_us
        ) * fraction
        self._drs.output.value = self._servo_duty(pulse, self._drs.frequency_hz)
        self._drs.current_pulse_us = pulse
        if fraction >= 1:
            self._drs.transition_target_pulse_us = None
            self._schedule_drs_release()

    def _release_drs_if_due(self) -> None:
        with self._drs_lock:
            if self._drs is None:
                return
            try:
                self._advance_drs()
                if (self._drs.output is not None and self._drs.pulse_release_at_monotonic is not None
                        and time.monotonic() >= self._drs.pulse_release_at_monotonic):
                    self._drs.output.close()
                    self._drs.output = None
                    self._drs.pulse_release_at_monotonic = None
                    LOG.info("DRS PWM released after movement to %s (position unverified)", self._drs.state)
            except Exception as exc:
                self._drs_failed(exc)
            self._publish_drs_status()

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
                    control_type=str(cfg.get("control_type", "binary")),
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

                is_headlight_mode = circuit.control_type == HEADLIGHT_MODE
                initial_state = circuit.boot_default if not is_headlight_mode or circuit.boot_default != "AUTO" else STATE_OFF
                output = OutputDevice(
                    pin=circuit.output_pin,
                    active_high=circuit.output_active_high,
                    initial_value=(initial_state == STATE_ON),
                )

                switch = None
                switch_on = None
                switch_auto = None

                if circuit.switch_mode == MODE_BINARY:
                    if circuit.switch_pin is not None:
                        switch = self._make_button(int(circuit.switch_pin), circuit.switch_pull_up,
                                                   circuit.switch_active_high)
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
                    requested_state=initial_state,
                    effective_state=initial_state,
                    source=SOURCE_REMOTE,
                    mode=circuit.boot_default.upper() if is_headlight_mode else None,
                )
                self._circuits[circuit_key] = runtime

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

    def _publish(self, topic: str, payload: str, retain: Optional[bool] = None, qos=None) -> None:
        if not self._mqtt_connected:
            return
        if retain is None:
            retain = MQTT["retain_state"]
        self._client.publish(topic, payload, qos=MQTT["qos"] if qos is None else qos, retain=retain)
        LOG.debug("Published %s => %s", topic, payload)

    @staticmethod
    def _make_button(pin: int, pull_up: Optional[bool], active_high: bool) -> PolledInput:
        """Preserve input/pull semantics without requesting edge interrupts."""
        kwargs = {"pin": pin, "pull_up": pull_up}
        if pull_up is None:
            kwargs["active_state"] = bool(active_high)
        return PolledInput(**kwargs)

    def _binary_switch_forced_on(self, runtime: CircuitRuntime) -> bool:
        if runtime.switch is None:
            return False
        raw_pressed = runtime.switch.is_pressed
        if runtime.config.switch_pull_up is None:
            return bool(raw_pressed)
        return bool(raw_pressed) if runtime.config.switch_active_high else not bool(raw_pressed)

    def _three_position_active(self, button: Optional[PolledInput], active_high: bool, pull_up: Optional[bool]) -> bool:
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

    @staticmethod
    def _is_headlight_mode(runtime: CircuitRuntime) -> bool:
        return runtime.config.name == "headlight" and runtime.config.control_type == HEADLIGHT_MODE

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
        previous_state = runtime.effective_state
        previous_source = runtime.source
        if runtime.config.switch_mode == MODE_BINARY:
            if self._is_headlight_mode(runtime) and runtime.mode == "AUTO" and self._ambient_auto_enabled(runtime):
                effective = self._ambient_auto_state(runtime)
                source = SOURCE_AUTO
            elif self._is_headlight_mode(runtime):
                effective = runtime.requested_state
                source = SOURCE_REMOTE
            elif self._ambient_auto_enabled(runtime):
                # Preserve the established behaviour for legacy binary profiles.
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

        if effective != previous_state:
            if effective == STATE_ON:
                runtime.output.on()
            else:
                runtime.output.off()

        runtime.effective_state = effective
        runtime.source = source
        if publish or effective != previous_state or source != previous_source:
            self._publish_runtime(runtime)

    def _publish_runtime(self, runtime: CircuitRuntime) -> None:
        trike = runtime.config.trike
        name = runtime.config.name
        base = self._base_topic(trike, name)
        self._publish(f"{base}/requested_state", runtime.requested_state)
        self._publish(f"{base}/state", runtime.effective_state)
        self._publish(f"{base}/source", runtime.source)
        if self._is_headlight_mode(runtime):
            self._publish(f"{base}/mode", runtime.mode or STATE_OFF)
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
            self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/mode", self._drs.mode)
            self._publish(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/source", self._drs.source)
            self._publish_drs_status(force=True)

    def _gpio_evidence(self) -> dict:
        """Read GPIO without changing outputs. This does not prove external loads moved."""
        errors = []
        output_count = input_count = 0
        for key, runtime in self._circuits.items():
            output_count += 1
            try:
                expected = runtime.effective_state == STATE_ON
                if bool(runtime.output.value) != expected:
                    errors.append(f"{key}:output_readback_mismatch")
                for switch in (runtime.switch, runtime.switch_on, runtime.switch_auto):
                    if switch is not None:
                        input_count += 1
                        _ = switch.is_pressed
            except Exception as exc:
                errors.append(f"{key}:{type(exc).__name__}")
        for key, runtime in self._sense_inputs.items():
            input_count += 1
            try:
                _ = runtime.button.is_pressed
            except Exception as exc:
                errors.append(f"{key}:{type(exc).__name__}")
        with self._drs_lock:
            if self._drs is not None:
                try:
                    if self._drs.output is not None:
                        output_count += 1
                        expected = self._servo_duty(self._drs.current_pulse_us, self._drs.frequency_hz)
                        if abs(self._drs.output.value - expected) > 0.005:
                            errors.append("drs:pwm_readback_mismatch")
                except Exception as exc:
                    errors.append(f"drs:{type(exc).__name__}")
        if output_count == 0:
            errors.append("no_gpio_outputs_configured")
        evidence = {"schema": "hpr.gpio_evidence.v1", "outputs_checked": output_count,
                "inputs_checked": input_count, "errors": errors,
                "device": "online" if not errors else "failed",
                "reason": "gpio_pin_readbacks_match" if not errors else "gpio_pin_readback_failed",
                "input_monitoring": "polled_debounced"}
        if getattr(self, "_telemetry", None) is not None:
            evidence["adc"] = self._telemetry.health_snapshot()
        return evidence

    def _publish_gpio_evidence(self) -> None:
        if self._mqtt_connected:
            self._publish(MQTT["gpio_evidence_topic"], json.dumps(self._gpio_evidence()), retain=False)

    def _on_switch_event(self, circuit_key: str) -> None:
        runtime = self._circuits[circuit_key]
        self._apply_logic(runtime, publish=True)

    def _on_sense_input_event(self, input_key: str) -> None:
        runtime = self._sense_inputs[input_key]
        self._publish_sense_input(runtime)
        if self._drs is not None and self._drs.mode == "RIDER_CONTROL" and input_key == self._key(DRS["trike"], DRS["rider_control_input"]):
            self._apply_drs_rider_switch(publish=True)

    def _poll_inputs(self) -> None:
        for key, runtime in self._sense_inputs.items():
            if self._sense_input_state(runtime) != runtime.last_state:
                self._on_sense_input_event(key)
        for key, runtime in self._circuits.items():
            if any(button is not None for button in (runtime.switch, runtime.switch_on, runtime.switch_auto)):
                if self._switch_state_payload(runtime) != runtime.last_switch_state:
                    self._on_switch_event(key)

    def _queue_event(self, kind, message=None) -> None:
        try:
            self._events.put_nowait((kind, message))
        except queue.Full:
            now = time.monotonic()
            if now - getattr(self, "_last_queue_warning", 0) > 30:
                LOG.warning("GPIO event queue full; request rejected")
                self._last_queue_warning = now

    def _drain_events(self) -> None:
        for _ in range(32):
            try:
                kind, message = self._events.get_nowait()
            except queue.Empty:
                return
            if kind == "command":
                self._handle_message(message)
            elif kind == "connected":
                if SERVICE["republish_state_on_reconnect"]:
                    self._publish_all_states()
                    self._telemetry.publish_latest()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        LOG.info("MQTT connected: rc=%s", reason_code)
        self._mqtt_connected = not (reason_code.is_failure if hasattr(reason_code, "is_failure") else reason_code != 0)
        if not self._mqtt_connected:
            return
        for runtime in self._circuits.values():
            base = self._base_topic(runtime.config.trike, runtime.config.name)
            client.subscribe(f"{base}/set", qos=MQTT["qos"])
        if self._drs is not None:
            client.subscribe(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/set", qos=MQTT["qos"])
            client.subscribe(f"{MQTT['topic_root']}/{DRS['trike']}/control/drs/command", qos=1)
        self._queue_event("connected")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._mqtt_connected = False
        LOG.warning("MQTT disconnected: rc=%s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        self._queue_event("command", msg)

    def _handle_message(self, msg) -> None:
        if (getattr(self, '_drs', None) is not None and
                msg.topic == f"{MQTT.get('topic_root', 'hpr')}/{DRS['trike']}/control/drs/command"):
            self._handle_drs_command(msg)
            return
        if msg.retain:
            LOG.info("Ignoring retained actuator command on %s", msg.topic)
            return
        payload = self._sanitize_payload(msg.payload)
        topic_root = MQTT.get("topic_root", "hpr")
        if self._drs is not None and msg.topic == f"{topic_root}/{DRS['trike']}/control/drs/set":
            state = {"ON": "OPEN", "OFF": "CLOSED", "CLOSE": "CLOSED"}.get(payload, payload)
            if state not in ("OPEN", "CLOSED", "RIDER_CONTROL"):
                LOG.warning("Ignoring invalid DRS payload on %s: %r", msg.topic, payload)
                return
            protocol = getattr(self, '_drs_protocol', None)
            if protocol is not None:
                protocol.last_command_id = None
            LOG.info('DRS legacy command mode=%s (uncorrelated, position unverified)', state)
            self._set_drs_mode(state)
            return
        for runtime in self._circuits.values():
            base = self._base_topic(runtime.config.trike, runtime.config.name)
            if msg.topic == f"{base}/set":
                if self._is_headlight_mode(runtime):
                    # The central boot mode, not an old retained command, owns
                    # the initial setting after a reconnect or service restart.
                    if payload not in (STATE_ON, STATE_OFF, "AUTO"):
                        LOG.warning("Ignoring invalid headlight mode on %s: %r", msg.topic, payload)
                        return
                    runtime.mode = payload
                    if payload in (STATE_ON, STATE_OFF):
                        runtime.requested_state = payload
                    self._apply_logic(runtime, publish=True)
                    return
                if payload not in (STATE_ON, STATE_OFF):
                    LOG.warning("Ignoring invalid payload on %s: %r", msg.topic, payload)
                    return
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
        if getattr(self, '_drs_clock', None) is not None:
            self._drs_clock.start()
        self._client.connect_async(MQTT["host"], MQTT["port"], keepalive=60)
        self._client.loop_start()
        reload_worker = getattr(self, '_credential_reload', None)
        if reload_worker is not None:
            reload_worker.thread.start()
        self._publish_all_states()
        self._telemetry.start()
        next_gpio_evidence = 0.0
        try:
            while not self._stop:
                self._drain_events()
                self._poll_inputs()
                now = time.monotonic()
                if now >= next_gpio_evidence:
                    self._publish_gpio_evidence()
                    next_gpio_evidence = now + 5.0
                self._handle_remote_pulse_expiry()
                self._release_drs_if_due()
                for runtime in self._circuits.values():
                    if self._ambient_auto_enabled(runtime) and (
                        not self._is_headlight_mode(runtime) or runtime.mode == "AUTO"
                    ):
                        self._apply_logic(runtime, publish=False)
                    elif runtime.config.switch_mode == MODE_THREE_POSITION and runtime.config.auto_enabled:
                        if self._three_position_state(runtime) == POSITION_AUTO:
                            self._apply_logic(runtime, publish=False)
                time.sleep(float(SERVICE["poll_interval_seconds"]))
        finally:
            if reload_worker is not None:
                reload_worker.stop()
            if getattr(self, '_drs_clock', None) is not None:
                self._drs_clock.stop()
            self._telemetry.stop()
            self._mqtt_connected = False
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
    service._credential_reload = CredentialReload(service._client, config)

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
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, lambda *_: service._credential_reload.requested.set())
    return service.start()


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

import yaml

from .gpio_profile import validate_gpio


DEFAULT_CONFIG_PATH = "/etc/hpr/hpr.yaml"
TRIKE_IDS = {f"trike{i}" for i in range(1, 7)}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str
    password: str


class HprConfig:
    def __init__(self, data: dict[str, Any], path: str) -> None:
        self.data = data
        self.path = path
        self.trike_id = self.require_str("hpr.trike_id")
        self.display_name = self.require_str("hpr.display_name")
        self.topic_root = self.require_str("hpr.topic_root")
        self.environment = self.require_str("hpr.environment")
        self.mqtt = MqttConfig(
            host=self.require_str("network.mqtt.host"),
            port=int(self.require("network.mqtt.port")),
            username=self.require_str("network.mqtt.username"),
            password=self.require_str("network.mqtt.password"),
        )
        home_assistant_host = self.require_str("network.home_assistant.host")
        self.require("network.home_assistant.port")
        self.require_str("network.home_assistant.url")
        if self.mqtt.host.lower() == "auto":
            raise ConfigError("network.mqtt.host must be resolved before Pi deployment")
        if home_assistant_host.lower() == "auto":
            raise ConfigError("network.home_assistant.host must be resolved before Pi deployment")
        self.validate()

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None or value == "":
            raise ConfigError(f"missing required config value: {dotted}")
        if isinstance(value, str) and value == "CHANGE_ME":
            raise ConfigError(f"config value must be changed before runtime: {dotted}")
        return value

    def require_str(self, dotted: str) -> str:
        value = self.require(dotted)
        if not isinstance(value, str):
            raise ConfigError(f"config value must be a string: {dotted}")
        return value

    def enabled(self, service: str) -> bool:
        return bool(self.get(f"services.{service}.enabled", False))

    def topic(self, *parts: str) -> str:
        clean = [self.topic_root.strip("/"), self.trike_id.strip("/")]
        clean.extend(part.strip("/") for part in parts if part)
        return "/".join(clean)

    def expand(self, value: str) -> str:
        return value.format(
            trike_id=self.trike_id,
            topic_root=self.topic_root,
            display_name=self.display_name,
            environment=self.environment,
        )

    def resolve_topic(self, value: str | None, *default_parts: str) -> str:
        if value:
            return self.expand(value).strip("/")
        return self.topic(*default_parts)

    def validate(self) -> None:
        if self.trike_id not in TRIKE_IDS:
            raise ConfigError(f"hpr.trike_id must be one of {sorted(TRIKE_IDS)}")
        self.require_str("pi.hostname")
        if self.mqtt.port <= 0:
            raise ConfigError("network.mqtt.port must be positive")
        if self.get("tailscale.enabled", False):
            hostname = self.get("tailscale.hostname") or self.get("pi.hostname")
            if not hostname:
                raise ConfigError("tailscale.hostname or pi.hostname is required when tailscale is enabled")
        controller_addresses = []
        ble_services_enabled = any(
            self.enabled(name) for name in ("tpms", "heart_rate", "power_cadence", "derailleur")
        )
        for role in ("telemetry", "heart_rate"):
            address = str(self.get(f"bluetooth.roles.{role}.controller_address", "") or "").upper()
            if address and not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
                raise ConfigError(f"bluetooth.roles.{role}.controller_address is not a valid MAC")
            if address:
                controller_addresses.append(address)
        if ble_services_enabled and len(controller_addresses) != 2:
            raise ConfigError(
                "enabled BLE services require stable telemetry and heart_rate controller addresses"
            )
        if len(controller_addresses) != len(set(controller_addresses)):
            raise ConfigError("Bluetooth telemetry and heart_rate roles must use different controllers")
        if self.enabled("tpms"):
            sensors = self.get("tpms.sensors", {})
            enabled = [(name, cfg) for name, cfg in sensors.items() if cfg.get("enabled")]
            if not enabled:
                raise ConfigError("services.tpms is enabled but no tpms.sensors entries are enabled")
            for name, sensor in enabled:
                for key in ("mac", "decode_profile"):
                    if not sensor.get(key) or sensor.get(key) == "CHANGE_ME":
                        raise ConfigError(f"tpms.sensors.{name}.{key} is required")
        if self.enabled("gps"):
            gps_device = str(self.get("gps.device", "") or "")
            if gps_device != "auto" and not gps_device.startswith("/dev/serial/by-id/"):
                raise ConfigError("gps.device must be auto or use /dev/serial/by-id/")
            for key in (
                "stale_timeout_seconds",
                "wait_timeout_seconds",
                "reconnect_initial_seconds",
                "reconnect_max_seconds",
            ):
                value = float(self.get(f"gps.{key}", 1))
                if value <= 0:
                    raise ConfigError(f"gps.{key} must be positive")
            if float(self.get("gps.reconnect_max_seconds", 15)) < float(
                self.get("gps.reconnect_initial_seconds", 1)
            ):
                raise ConfigError("gps.reconnect_max_seconds must be >= gps.reconnect_initial_seconds")
        if self.enabled("gpio_control"):
            if self.get("gpio.enabled") is False:
                raise ConfigError("services.gpio_control.enabled conflicts with gpio.enabled=false")
            try:
                validate_gpio(self.get("gpio", {}), strict="enabled" in self.get("gpio", {}))
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
        if self.enabled("power_cadence"):
            devices = self.get("power_cadence.devices", {})
            for name, device in devices.items():
                if device.get("enabled"):
                    if not device.get("mac") or device.get("mac") == "CHANGE_ME":
                        raise ConfigError(f"power_cadence.devices.{name}.mac is required")
                    for key in (
                        "scan_timeout_seconds",
                        "connect_timeout_seconds",
                        "gatt_timeout_seconds",
                        "notification_stale_timeout_seconds",
                        "reconnect_initial_seconds",
                        "reconnect_max_seconds",
                    ):
                        try:
                            value = float(device.get(key, 1))
                        except (TypeError, ValueError) as exc:
                            raise ConfigError(
                                f"power_cadence.devices.{name}.{key} must be numeric"
                            ) from exc
                        if value <= 0:
                            raise ConfigError(
                                f"power_cadence.devices.{name}.{key} must be positive"
                            )
                    reconnect_initial = float(device.get("reconnect_initial_seconds", 1))
                    reconnect_max = float(device.get("reconnect_max_seconds", 15))
                    if reconnect_max < reconnect_initial:
                        raise ConfigError(
                            f"power_cadence.devices.{name}.reconnect_max_seconds must be "
                            ">= reconnect_initial_seconds"
                        )
        if self.enabled("heart_rate"):
            monitors = self.get("heart_rate.monitors", {})
            enabled_monitors = [m for m in monitors.values() if m.get("enabled")]
            if not enabled_monitors:
                raise ConfigError("services.heart_rate is enabled but no heart_rate.monitors entries are enabled")
            for name, monitor in monitors.items():
                if monitor.get("enabled") and (not monitor.get("mac") or monitor.get("mac") == "CHANGE_ME"):
                    raise ConfigError(f"heart_rate.monitors.{name}.mac is required")
        if self.enabled("derailleur"):
            self.require_str("derailleur.mac")
            for key in (
                "scan_timeout_seconds",
                "connect_timeout_seconds",
                "notification_stale_timeout_seconds",
                "reconnect_initial_seconds",
                "reconnect_max_seconds",
            ):
                if float(self.get(f"derailleur.{key}", 0)) <= 0:
                    raise ConfigError(f"derailleur.{key} must be positive")
            if float(self.get("derailleur.reconnect_max_seconds", 15)) < float(
                self.get("derailleur.reconnect_initial_seconds", 1)
            ):
                raise ConfigError(
                    "derailleur.reconnect_max_seconds must be >= reconnect_initial_seconds"
                )
        if self.enabled("video"):
            cameras = list(enabled_items(self.get("video.cameras", {})))
            if not cameras:
                raise ConfigError("services.video is enabled but no video.cameras entries are enabled")
            paths: set[str] = set()
            for name, camera in cameras:
                device = str(camera.get("device") or "")
                if not device.startswith(("/dev/v4l/by-id/", "/dev/v4l/by-path/")):
                    raise ConfigError(
                        f"video.cameras.{name}.device must use /dev/v4l/by-id/ or /dev/v4l/by-path/"
                    )
                for size_key in ("video_size", "stream_size"):
                    size = str(camera.get(size_key) or "")
                    if not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", size):
                        raise ConfigError(
                            f"video.cameras.{name}.{size_key} must use WIDTHxHEIGHT"
                        )
                if not camera.get("path"):
                    raise ConfigError(f"video.cameras.{name}.path is required")
                path = str(camera["path"])
                if path in paths:
                    raise ConfigError(f"video camera path is duplicated: {path}")
                paths.add(path)
            checksum = str(self.get("video.mediamtx_sha256", "") or "")
            if not re.fullmatch(r"[0-9A-Fa-f]{64}", checksum):
                raise ConfigError("video.mediamtx_sha256 must be a 64-digit SHA256")


def load_config(path: str = DEFAULT_CONFIG_PATH) -> HprConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError("config file root must be a mapping")
    return HprConfig(data, str(config_path))


def config_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help=f"config path, default {DEFAULT_CONFIG_PATH}")
    return parser


def enabled_items(mapping: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for name, cfg in mapping.items():
        if isinstance(cfg, dict) and cfg.get("enabled", False):
            yield name, cfg

"""Validation and identity for centrally managed GPIO profiles (no hardware I/O)."""

import hashlib
import json
import re


def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def validate_gpio(gpio, *, strict=False):
    if not isinstance(gpio, dict):
        raise ValueError("gpio must be a mapping")
    if gpio.get("numbering", "BCM") != "BCM":
        raise ValueError("gpio.numbering must be BCM")
    used = {}

    def pin(value, path):
        if type(value) is not int or not 0 <= value <= 27:
            raise ValueError(f"{path} must be a BCM GPIO number from 0 to 27")
        if value in used:
            raise ValueError(f"{path} conflicts with {used[value]} on GPIO{value}")
        used[value] = path

    for collection, key in (("circuits", "output_pin"), ("sense_inputs", "pin"), ("pwm_outputs", "pin")):
        items = gpio.get(collection, {})
        if not isinstance(items, dict):
            raise ValueError(f"gpio.{collection} must be a mapping")
        for name, item in items.items():
            path = f"gpio.{collection}.{name}"
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not isinstance(item, dict):
                raise ValueError(f"Invalid GPIO channel: {path}")
            if type(item.get("enabled", False)) is not bool:
                raise ValueError(f"{path}.enabled must be boolean")
            if not item.get("enabled") and not item.get("reserved", False):
                continue
            pin(item.get(key, item.get("pin")), f"{path}.{key}")
            if collection == "circuits":
                allowed_defaults = ("ON", "OFF", "AUTO") if item.get("control_type") == "headlight_mode" else ("ON", "OFF")
                if item.get("boot_default") not in allowed_defaults:
                    raise ValueError(f"{path}.boot_default must be one of {', '.join(allowed_defaults)}")
                if strict and type(item.get("output_active_high")) is not bool:
                    raise ValueError(f"{path}.output_active_high is UNKNOWN_REVIEW_REQUIRED")
                for switch in ("switch_pin", "switch_pin_on", "switch_pin_auto"):
                    if item.get(switch) is not None:
                        pin(item[switch], f"{path}.{switch}")
            elif collection == "sense_inputs":
                if item.get("pull_up") is not None and type(item["pull_up"]) is not bool:
                    raise ValueError(f"{path}.pull_up must be true, false or null")
                if type(item.get("active_high")) is not bool:
                    raise ValueError(f"{path}.active_high must be boolean")
                if item.get("state_semantics", "legacy") not in ("legacy", "electrical"):
                    raise ValueError(f"{path}.state_semantics is unsupported")
            elif item.get("enabled"):
                frequency = item.get("frequency_hz")
                close_pulse = item.get("close_pulse_us")
                open_pulse = item.get("open_pulse_us")
                if frequency != 333:
                    raise ValueError(f"{path}.frequency_hz must be 333 for the PTK 7462MG")
                for field, value in (("close_pulse_us", close_pulse), ("open_pulse_us", open_pulse)):
                    if type(value) is not int or not 500 <= value <= 2500:
                        raise ValueError(f"{path}.{field} must be an integer from 500 to 2500")
                if close_pulse == open_pulse:
                    raise ValueError(f"{path} open and close pulses must differ")
                transition = item.get("transition_seconds", 0)
                if not isinstance(transition, (int, float)) or not 0 <= transition <= 5:
                    raise ValueError(f"{path}.transition_seconds must be from 0 to 5 seconds")
                behavior = item.get("switch_behavior", "remote_open_close")
                if behavior not in ("remote_open_close", "pulse_release"):
                    raise ValueError(f"{path}.switch_behavior is unsupported")
                if behavior == "pulse_release":
                    duration = item.get("pulse_release_seconds")
                    if not isinstance(duration, (int, float)) or not 0.1 <= duration <= 5:
                        raise ValueError(f"{path}.pulse_release_seconds must be from 0.1 to 5 seconds")
    i2c = gpio.get("i2c", {})
    if not isinstance(i2c, dict):
        raise ValueError("gpio.i2c must be a mapping")
    if i2c.get("enabled"):
        pin(i2c.get("sda_pin"), "gpio.i2c.sda_pin")
        pin(i2c.get("scl_pin"), "gpio.i2c.scl_pin")
        if (i2c.get("bus"), i2c.get("sda_pin"), i2c.get("scl_pin")) != (1, 2, 3):
            raise ValueError("Current ADC driver requires I2C bus 1 on BCM2/BCM3")
    if gpio.get("enabled") and strict and gpio.get("review_status") != "CONFIRMED":
        raise ValueError("GPIO hardware review is UNKNOWN_REVIEW_REQUIRED")


def validate_profile(profile, trike_id):
    if not isinstance(profile, dict) or set(profile) != {"schema_version", "trike_id", "gpio_enabled", "gpio"}:
        raise ValueError("Invalid GPIO profile fields")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1 or profile["trike_id"] != trike_id:
        raise ValueError("GPIO profile schema or trike identity mismatch")
    if type(profile["gpio_enabled"]) is not bool:
        raise ValueError("gpio_enabled must be boolean")
    if not isinstance(profile["gpio"], dict) or profile["gpio_enabled"] != profile["gpio"].get("enabled"):
        raise ValueError("GPIO capability flags do not match")
    validate_gpio(profile["gpio"], strict=profile["gpio_enabled"])

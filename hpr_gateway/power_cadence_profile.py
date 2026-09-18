"""Validation and hashing for centrally managed pedal-power assignments."""

import hashlib
import json
import re


MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")


def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def validate_profile(profile, trike_id):
    if not isinstance(profile, dict) or set(profile) != {"schema_version", "trike_id", "power_cadence_enabled", "devices"}:
        raise ValueError("Invalid pedal-power profile fields")
    if profile["schema_version"] != 1 or profile["trike_id"] != trike_id:
        raise ValueError("Pedal-power profile schema or trike identity mismatch")
    if type(profile["power_cadence_enabled"]) is not bool or not isinstance(profile["devices"], dict):
        raise ValueError("Invalid pedal-power capability flags")
    for name, device in profile["devices"].items():
        if not isinstance(device, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
            raise ValueError(f"Invalid pedal-power device {name}")
        if device.get("enabled") and not MAC_RE.fullmatch(str(device.get("mac") or "").upper()):
            raise ValueError(f"Pedal-power device {name} has an invalid MAC")
    if profile["power_cadence_enabled"] != any(bool(item.get("enabled")) for item in profile["devices"].values()):
        raise ValueError("Pedal-power profile capability flags do not match")

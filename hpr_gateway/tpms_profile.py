"""Validation and hashing for centrally managed TPMS assignments."""

import hashlib
import json
import re


MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
POSITIONS = {"front_left", "front_right", "rear", "spare"}


def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def validate_profile(profile, trike_id):
    if not isinstance(profile, dict) or set(profile) != {"schema_version", "trike_id", "tpms_enabled", "sensors"}:
        raise ValueError("Invalid TPMS profile fields")
    if profile["schema_version"] != 1 or profile["trike_id"] != trike_id:
        raise ValueError("TPMS profile schema or trike identity mismatch")
    if type(profile["tpms_enabled"]) is not bool or not isinstance(profile["sensors"], dict):
        raise ValueError("Invalid TPMS profile capability flags")
    positions = set()
    for name, sensor in profile["sensors"].items():
        if not isinstance(sensor, dict) or not re.fullmatch(r"tpms[1-9][0-9]*", str(name)):
            raise ValueError(f"Invalid TPMS sensor {name}")
        if not sensor.get("enabled"):
            continue
        mac = str(sensor.get("mac") or "").upper()
        position = str(sensor.get("position") or "")
        if not MAC_RE.fullmatch(mac) or position not in POSITIONS or not sensor.get("decode_profile"):
            raise ValueError(f"TPMS sensor {name} is incomplete")
        if position != "spare" and position in positions:
            raise ValueError(f"Duplicate TPMS wheel position {position}")
        positions.add(position)
    if profile["tpms_enabled"] != any(bool(item.get("enabled")) for item in profile["sensors"].values()):
        raise ValueError("TPMS profile capability flags do not match")

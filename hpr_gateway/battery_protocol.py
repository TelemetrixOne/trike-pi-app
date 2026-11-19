"""Bounded, fresh battery-swap requests. No GPIO or network I/O."""
import json
import math
import uuid


def parse_swap_request(payload, retained, now, clock_ready):
    if retained or not clock_ready or len(payload) > 2048:
        raise ValueError('retained_or_clock_unavailable_or_oversized')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_field')
            result[key] = value
        return result
    try:
        value = json.loads(payload, object_pairs_hook=unique)
        fields = {'schema_version', 'request_id', 'action', 'issued_at', 'expires_at'}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('invalid_fields')
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['action'] != 'BATTERY_SWAPPED':
            raise ValueError('invalid_action_or_schema')
        identity = value['request_id']
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
            raise ValueError('invalid_request_id')
        issued, expires = value['issued_at'], value['expires_at']
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (issued, expires)):
            raise ValueError('invalid_timestamps')
        if not (0 < expires-issued <= 5 and now-5 <= issued <= now+2 and now < expires):
            raise ValueError('expired_or_invalid_window')
        return value
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError('invalid_envelope') from exc

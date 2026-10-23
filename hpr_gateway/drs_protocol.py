"""DRS-only bounded, expiring command identities and truthful status metadata."""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import subprocess
import threading
import time
import uuid


def finite_timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


class DRSClock:
    """NTP readiness outside the GPIO/actuation loop; failure is closed."""
    def __init__(self):
        self._ready = False
        self._checked = float('-inf')
        self._stop = threading.Event()

    @property
    def ready(self):
        return self._ready and time.monotonic() - self._checked < 10

    def start(self):
        def poll():
            while not self._stop.is_set():
                try:
                    self._ready = subprocess.check_output(
                        ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                        text=True, timeout=1, stderr=subprocess.DEVNULL).strip() == 'yes'
                except (OSError, subprocess.SubprocessError):
                    self._ready = False
                self._checked = time.monotonic()
                self._stop.wait(5)
        self._thread = threading.Thread(target=poll, name='drs-clock', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()


class DRSProtocol:
    def __init__(self, capacity=256):
        self.boot_id = str(uuid.uuid4())
        self.seq = 0
        self.last_command_id = None
        self.seen = OrderedDict()
        self.capacity = capacity
        self.last_status = None
        self.next_status_at = 0.0

    def event(self, mode, target, phase):
        self.seq += 1
        return {'schema_version': 1, 'boot_id': self.boot_id, 'seq': self.seq,
                'published_at': time.time(), 'mode': mode,
                'target': 'CLOSED' if target == 'CLOSE' else target,
                'phase': phase, 'last_command_id': self.last_command_id,
                'position_verified': False}

    def parse(self, payload, retained, clock_ready):
        """Return (command, immediate rejection/duplicate) without hardware I/O."""
        now, monotonic = time.time(), time.monotonic()
        for key, value in list(self.seen.items()):
            if monotonic - value[0] > 60:
                del self.seen[key]
        command = {}
        try:
            if len(payload) > 2048:
                raise ValueError('oversized')
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate_key')
                    result[key] = value
                return result
            command = json.loads(payload, object_pairs_hook=unique)
            if not isinstance(command, dict):
                raise ValueError('not_object')
            ident = command.get('command_id')
            if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident.lower():
                raise ValueError('invalid_command_id')
            command['command_id'] = ident.lower()
        except (ValueError, TypeError, AttributeError, UnicodeDecodeError, RecursionError):
            return {}, {'result': 'rejected', 'reason': 'invalid_envelope', 'command_id': None}
        ident = command['command_id']
        if ident in self.seen:
            original = self.seen[ident][1]
            return command, {'result': 'duplicate', 'reason': 'already_' + original,
                             'original_result': original, 'command_id': ident}
        reason, result = None, 'rejected'
        if retained:
            reason = 'retained_command'
        elif type(command.get('schema_version')) is not int or command['schema_version'] != 1:
            reason = 'unsupported_schema'
        elif command.get('mode') not in ('OPEN', 'CLOSED', 'RIDER_CONTROL'):
            reason = 'invalid_mode'
        elif not all(finite_timestamp(command.get(key))
                     for key in ('issued_at', 'expires_at')):
            reason = 'invalid_timestamps'
        elif not 0 < command['expires_at'] - command['issued_at'] <= 5:
            reason = 'invalid_ttl'
        elif not clock_ready:
            reason = 'clock_unsynchronised'
        elif command['expires_at'] <= now or command['issued_at'] < now - 5:
            reason, result = 'command_expired', 'expired'
        elif command['issued_at'] > now + 2:
            reason = 'issued_in_future'
        elif len(self.seen) >= self.capacity:
            reason = 'command_cache_full'
        if reason:
            self.remember(ident, result)
            return command, {'result': result, 'reason': reason, 'command_id': ident}
        return command, None

    def remember(self, ident, result):
        # Never evict a live identity to admit another command. Rejecting at
        # capacity prevents a QoS duplicate from becoming a second actuation.
        if ident and (ident in self.seen or len(self.seen) < self.capacity):
            self.seen[ident] = (time.monotonic(), result)

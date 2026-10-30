"""Fresh demo requests and a bounded durable identity journal; no hardware I/O."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid


def request_identity(raw):
    try:
        if len(raw) > 2048: return None
        return str(uuid.UUID(json.loads(raw)['request_id']))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeDecodeError):
        return None


def envelope(raw, retained=False, clock_ready=True, now=None):
    now = time.time() if now is None else now
    if retained or not clock_ready or len(raw) > 2048:
        raise ValueError('retained, unsynchronised or oversized demo request')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('duplicate request field')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != {'schema_version', 'action', 'request_id', 'issued_at', 'expires_at'}:
            raise ValueError('versioned request with issue time and expiry required')
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['action'] != 'START':
            raise ValueError('unsupported demo action/schema')
        value['request_id'] = str(uuid.UUID(value['request_id']))
        if not all(type(value[k]) in (float, int) and math.isfinite(value[k]) for k in ('issued_at', 'expires_at')):
            raise ValueError('invalid request clock')
        if not (0 < value['expires_at'] - value['issued_at'] <= 5 and value['expires_at'] > now
                and now - 5 <= value['issued_at'] <= now + 2):
            raise ValueError('expired or invalid demo request window')
        return value
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError('invalid demo request') from exc


class DemoJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS consumed (identity TEXT PRIMARY KEY, accepted_at REAL NOT NULL)')
        os.chmod(self.path, 0o600)

    def claim(self, identity, now=None):
        now = time.time() if now is None else now
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('DELETE FROM consumed WHERE accepted_at < ?', (now - 86400,))
            if db.execute('SELECT 1 FROM consumed WHERE identity=?', (identity,)).fetchone():
                return False
            if db.execute('SELECT COUNT(*) FROM consumed').fetchone()[0] >= 4096:
                raise ValueError('demo replay journal at capacity')
            db.execute('INSERT INTO consumed VALUES (?, ?)', (identity, now))
        return True

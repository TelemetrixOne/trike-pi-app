"""Fail-closed admission for operator maintenance, never for live device loops.

This is a read-side guard, NOT a distributed maintenance reservation. Central
session arming can race an admitted operation. Do not claim cross-host exclusion.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

import yaml

CONFIG = Path('/etc/hpr/hpr.yaml')
STATE = Path('/var/lib/hpr/maintenance')
LOCK_FD = 199
IDLE_PHASES = frozenset(('ended', 'draft'))
MAX_AGE = 15


class MaintenanceBlocked(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise MaintenanceBlocked('protection endpoint redirected')


def utc(value):
    if not isinstance(value, str):
        raise MaintenanceBlocked('missing server time')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError('timezone required')
        return stamp.astimezone(timezone.utc)
    except ValueError as exc:
        raise MaintenanceBlocked('invalid server time') from exc


def decision(payload, identity, previous, now):
    """Return durable evidence and admission; active evidence persists on denial."""
    if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
        raise MaintenanceBlocked('unknown protection schema')
    for key in ('trike_id', 'topic_root'):
        if payload.get(key) != identity[key]:
            raise MaintenanceBlocked('protection identity mismatch')
    if payload.get('enabled') is not True or payload.get('uses_pi') is not True:
        raise MaintenanceBlocked('Pi capability not enabled')
    if type(payload.get('blocked')) is not bool:
        raise MaintenanceBlocked('missing protection decision')
    stamp = utc(payload.get('server_time_utc'))
    if abs((now - stamp).total_seconds()) > MAX_AGE:
        raise MaintenanceBlocked('protection clock stale or skewed')
    old = utc(previous['server_time_utc']) if previous else None
    if old and stamp < old:
        raise MaintenanceBlocked('protection response went backwards')
    phase = payload.get('phase')
    admitted = payload['blocked'] is False and phase in IDLE_PHASES
    if previous and previous.get('latched') and (not admitted or stamp <= old):
        admitted = False
    evidence = {**identity, 'server_time_utc': stamp.isoformat(),
                'phase': phase, 'session_id': payload.get('session_id'), 'latched': not admitted}
    return evidence, admitted


def trusted_config():
    info = CONFIG.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise MaintenanceBlocked('protection configuration must be root-owned and not group/world writable')
    data = yaml.safe_load(CONFIG.read_text())
    identity = {key: data['hpr'][key] for key in ('trike_id', 'topic_root')}
    if any(not isinstance(v, str) or not v.strip() for v in identity.values()):
        raise MaintenanceBlocked('missing local identity')
    url = data['network']['maintenance']['protection_url']
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise MaintenanceBlocked('enrol a trusted HTTPS protection URL without credentials/query/fragment')
    return identity, url


def fetch(url):
    # Do not accept environment proxy settings or disable TLS verification.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = Request(url, headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise MaintenanceBlocked('protection HTTP status not 200')
        body = response.read(16385)
    if len(body) > 16384:
        raise MaintenanceBlocked('protection response too large')
    return json.loads(body)


def _check():
    """Caller holds the maintenance flock. No fallback, force or env override."""
    try:
        identity, url = trusted_config()
        path = STATE / 'protection.json'
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous and any(previous.get(k) != v for k, v in identity.items()):
            raise MaintenanceBlocked('durable protection identity changed; preserve and review')
        evidence, admitted = decision(fetch(url), identity, previous, datetime.now(timezone.utc))
        with tempfile.NamedTemporaryFile(mode='w', dir=STATE, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(evidence, handle); handle.flush(); os.fsync(handle.fileno())
        temporary.replace(path)
        directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        if not admitted:
            raise MaintenanceBlocked('session protected or not explicitly idle')
    except MaintenanceBlocked:
        raise
    except Exception as exc:
        # Never print URLs, endpoint responses, config contents or credentials.
        raise MaintenanceBlocked('protection evidence unavailable or invalid (' + type(exc).__name__ + ')') from exc


_inside = threading.local()


def check():
    # Guard bookkeeping must not recursively trigger its own mutation hooks.
    previous = getattr(_inside, 'checking', False)
    _inside.checking = True
    try: return _check()
    finally: _inside.checking = previous


@contextmanager
def admission():
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise MaintenanceBlocked('operator maintenance requires sudo and trusted local state')
    import fcntl
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = STATE.lstat()
    if STATE.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o077:
        raise MaintenanceBlocked('untrusted maintenance state directory')
    path = STATE / 'operation.lock'
    # Inherited open-file description allows nested supported scripts to share
    # a lease. Even inherited callers must acquire flock and fetch fresh state.
    try:
        info = os.fstat(LOCK_FD)
        inherited = os.path.samestat(info, path.stat())
    except OSError:
        inherited = False
    fd = LOCK_FD if inherited else os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if not inherited and fd != LOCK_FD:
        os.dup2(fd, LOCK_FD, inheritable=True); os.close(fd); fd = LOCK_FD
    os.set_inheritable(fd, True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check()
        yield
    except BlockingIOError as exc:
        raise MaintenanceBlocked('another local maintenance operation holds the lock') from exc
    finally:
        if not inherited: os.close(fd)


_boundary_enabled = False
_audit_registered = False


def _audit(event, args):
    if not _boundary_enabled or getattr(_inside, 'checking', False):
        return
    mutation = event in ('os.rename', 'os.remove', 'os.mkdir', 'os.rmdir', 'os.chmod',
                         'os.chown', 'os.symlink', 'os.link', 'os.truncate')
    if event == 'open':
        mutation = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if mutation or event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        check()


def recheck_if_guarded():
    """For non-process disruptive boundaries such as sending the console demo."""
    if _boundary_enabled:
        check()


def guarded_main(main):
    """Guard CLI admission and each Python-launched external process boundary."""
    global _boundary_enabled, _audit_registered
    try:
        with admission():
            if not _audit_registered:
                sys.addaudithook(_audit)
                _audit_registered = True
            original = subprocess.Popen.__init__
            def inherit_lock(process, *args, **kwargs):
                kwargs['pass_fds'] = tuple(set(kwargs.get('pass_fds', ())) | {LOCK_FD})
                return original(process, *args, **kwargs)
            subprocess.Popen.__init__ = inherit_lock
            _boundary_enabled = True
            try: return main()
            finally:
                _boundary_enabled = False
                subprocess.Popen.__init__ = original
    except MaintenanceBlocked as exc:
        print('Maintenance refused: ' + str(exc), file=sys.stderr)
        return 75


def cancellable_run(args):
    """Only for isolated candidate jobs; never cancel live service transactions."""
    import signal
    import time
    with subprocess.Popen(args, start_new_session=True) as process:
        try:
            while process.poll() is None:
                check()
                time.sleep(2)
        except MaintenanceBlocked:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)

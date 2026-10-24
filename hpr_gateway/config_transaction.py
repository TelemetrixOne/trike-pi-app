"""One cross-process configuration transaction lock, including legacy recovery."""
from contextlib import contextmanager
from functools import wraps
import importlib
import os
from pathlib import Path
import threading

_guard = threading.RLock()
_local = threading.local()
MARKER = '.config-single-writer-v2'
PENDING = {
    '.trike-config-sync-pending': 'trike_config_sync',
    '.gpio-sync-pending.json': 'gpio_config_sync',
    '.tpms-sync-pending.json': 'tpms_config_sync',
    '.power-cadence-sync-pending.json': 'power_cadence_config_sync',
}


def migrated(path):
    return Path(path).with_name(MARKER).is_file()


@contextmanager
def config_lock(path):
    # A separate, never-renamed inode protects read/modify/write AND recovery.
    key = str(Path(path).resolve())
    with _guard:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        with Path(path).with_name('.config-sync.lock').open('a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                if os.name == 'nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def serialized_config(*, scoped=False):
    def decorate(method):
        @wraps(method)
        def locked(self, *args, **kwargs):
            with config_lock(self.path):
                if scoped and migrated(self.path):
                    raise RuntimeError('Standalone config writer retired; use complete trike profile')
                if method.__name__ == 'apply':
                    pending = [name for name in PENDING if self.path.with_name(name).exists()]
                    if len(pending) > 1:
                        raise RuntimeError('Multiple interrupted config transactions; preserve and investigate')
                    if pending and PENDING[pending[0]] != self.__class__.__module__.split('.')[-1]:
                        module = importlib.import_module('hpr_gateway.services.' + PENDING[pending[0]])
                        module.ProfileApplier(str(self.path)).recover()
                return method(self, *args, **kwargs)
        return locked
    return decorate

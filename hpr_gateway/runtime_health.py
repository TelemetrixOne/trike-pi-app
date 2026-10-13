"""Read-only, bounded Pi resource telemetry, isolated from service checks."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone

METRICS_INTERVAL = 10
LOG_INTERVAL = 60
LOG_WINDOW_SECONDS = 1800
LOG_ENTRY_LIMIT = 20000


def cpu_counters(text: str) -> tuple[int, ...]:
    fields = text.splitlines()[0].split()
    if fields[0] != 'cpu' or len(fields) < 9:
        raise ValueError('Missing aggregate CPU counters')
    # Guest counters are already included in user/nice; never double-count them.
    values = tuple(int(value) for value in fields[1:9])
    if any(value < 0 for value in values):
        raise ValueError('Invalid CPU counters')
    return values


def cpu_usage(previous: tuple[int, ...] | None, current: tuple[int, ...]) -> float | None:
    if previous is None:
        return None
    delta = [new - old for old, new in zip(previous, current)]
    if any(value < 0 for value in delta) or sum(delta) <= 0:
        return None
    # Overall 0..100%, across all cores; iowait is not executing CPU work.
    return round(100 * (sum(delta) - delta[3] - delta[4]) / sum(delta), 1)


def memory_usage(text: str) -> float:
    values = {name: int(raw.strip().split()[0]) for name, raw in
              (line.split(':', 1) for line in text.splitlines())}
    total, available = values['MemTotal'], values['MemAvailable']
    if total <= 0 or not 0 <= available <= total:
        raise ValueError('Invalid memory counters')
    # Includes reclaimable cache as available, matching the existing health agent.
    return round(100 * (total - available) / total, 1)


def journal_counts() -> tuple[int, int]:
    result = subprocess.run([
        'journalctl', '--system', f'--since=-{LOG_WINDOW_SECONDS}s',
        '--priority=0..4', f'--lines={LOG_ENTRY_LIMIT + 1}', '--output=json',
        '--output-fields=__REALTIME_TIMESTAMP,PRIORITY', '--no-pager', '--quiet',
    ], text=True, capture_output=True, timeout=5, check=True)
    # Permissions/partial-journal errors must not look like a healthy zero count.
    if result.stderr.strip():
        raise RuntimeError('System journal query reported an error')
    lines = result.stdout.splitlines()
    if len(lines) > LOG_ENTRY_LIMIT:
        raise RuntimeError('Log window exceeds bounded query limit; counts unavailable')
    warnings = errors = 0
    for line in lines:
        priority = int(json.loads(line)['PRIORITY'])
        if priority == 4:
            warnings += 1
        elif 0 <= priority <= 3:
            errors += 1
        else:
            raise ValueError('Unexpected journal priority')
    return warnings, errors


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeHealth:
    """Collect off the MQTT/service-check thread; failures publish null, not zero."""

    def __init__(self, trike_id: str):
        self.trike_id = trike_id
        self.previous_cpu = None
        self.logs = {'warnings_count': None, 'errors_count': None,
                     'logs_observed_at': None, 'logs_complete': False}
        self.log_error = 'not_sampled'
        self.next_logs = 0.0
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.value = self.base()
        self.thread = threading.Thread(target=self.run, name='runtime-health', daemon=True)

    def base(self) -> dict:
        return {'schema': 'hpr.pi_system_metrics.v1', 'trike_id': self.trike_id,
                'cpu_usage_pct': None, 'memory_used_pct': None, 'cpu_temperature_c': None,
                'logs_window_seconds': LOG_WINDOW_SECONDS, 'logs_scope': 'system_journal',
                'logs_refresh_seconds': LOG_INTERVAL, 'sample_interval_seconds': METRICS_INTERVAL,
                **self.logs, 'observed_at': None, 'errors': ['not_sampled']}

    def collect(self) -> None:
        data, errors = self.base(), []
        try:
            current = cpu_counters(Path('/proc/stat').read_text())
            data['cpu_usage_pct'] = cpu_usage(self.previous_cpu, current)
            self.previous_cpu = current
            if data['cpu_usage_pct'] is None:
                errors.append('cpu_sample_unavailable')
        except (OSError, ValueError, IndexError):
            self.previous_cpu = None
            errors.append('cpu_read_failed')
        try:
            data['memory_used_pct'] = memory_usage(Path('/proc/meminfo').read_text())
        except (OSError, ValueError, KeyError):
            errors.append('memory_read_failed')
        try:
            temp = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
            if not -40 <= temp <= 150:
                raise ValueError('Invalid temperature')
            data['cpu_temperature_c'] = round(temp, 1)
        except (OSError, ValueError):
            errors.append('temperature_read_failed')
        now = time.monotonic()
        if now >= self.next_logs:
            self.next_logs = now + LOG_INTERVAL
            try:
                warnings, faults = journal_counts()
                self.logs = {'warnings_count': warnings, 'errors_count': faults,
                             'logs_observed_at': utc_now(), 'logs_complete': True}
                self.log_error = None
            except Exception as exc:
                self.logs = {'warnings_count': None, 'errors_count': None,
                             'logs_observed_at': None, 'logs_complete': False}
                self.log_error = f'logs_unavailable:{type(exc).__name__}'
        if self.log_error:
            errors.append(self.log_error)
        data.update(self.logs)
        data.update(observed_at=utc_now(), errors=errors)
        with self.lock:
            self.value = data

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.value))

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                self.collect()
            except Exception:
                # Keep unexpected collection failures out of the service-check loop.
                data = self.base()
                data.update(observed_at=utc_now(), errors=['runtime_collection_failed'])
                with self.lock:
                    self.value = data
            self.stopping.wait(METRICS_INTERVAL)

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=6)

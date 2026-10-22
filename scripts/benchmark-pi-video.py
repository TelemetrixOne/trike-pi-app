#!/usr/bin/env python3
"""Read-only BOTH-stream baseline/trial sample. Latency requires a real visual test.

Run with the gateway venv on the Pi. Stdout is JSON; no services are changed.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import time


def cpu():
    values = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def status():
    value = json.loads(Path('/run/hpr-video/video-status.json').read_text())
    if time.monotonic() - value['at'] > 3:
        raise RuntimeError('Stale video status')
    for name in ('front-capture', 'rear-capture', 'front-publish', 'rear-publish'):
        if name not in value['children']:
            raise RuntimeError('Both cameras must be active: missing ' + name)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--latency-front-ms', type=float)
    parser.add_argument('--latency-rear-ms', type=float)
    args = parser.parse_args()
    if not 30 <= args.duration <= 600:
        parser.error('duration must be 30..600 seconds')
    start = time.monotonic()
    first = status()
    old_cpu = cpu()
    samples, gaps = [], []
    timestamp = {}
    commands = {}
    for role in ('front', 'rear'):
        pid = first['children'][role + '-publish']['pid']
        command = Path(f'/proc/{pid}/cmdline').read_bytes().decode().replace('\0', ' ')
        timestamp[role] = ('clockoverlay name=burned_timestamp' in command
                           and first['children'][role + '-publish'].get('timestamp_visible', False))
        commands[role] = command
    old = first
    while time.monotonic() - start < args.duration:
        time.sleep(1)
        new = status()
        for name, child in first['children'].items():
            if name not in new['children'] or new['children'][name]['pid'] != child['pid']:
                raise RuntimeError('Worker changed during benchmark: ' + name)
            if name.endswith('-publish'):
                role = name.removesuffix('-publish')
                if timestamp[role] and not new['children'][name].get('timestamp_visible', False):
                    raise RuntimeError('Timestamp lost clock synchronisation: ' + role)
                gaps.append(time.monotonic() - new['children'][name]['at'])
                if new['children'][name]['frames'] <= old['children'][name]['frames']:
                    raise RuntimeError('No frame progress: ' + name)
        now_cpu = cpu()
        total, idle = now_cpu[0] - old_cpu[0], now_cpu[1] - old_cpu[1]
        samples.append(100 * (total - idle) / total)
        old_cpu, old = now_cpu, new
    frame_rates = {}
    for role in ('front', 'rear'):
        name = role + '-publish'
        a, b = first['children'][name], new['children'][name]
        frame_rates[role] = (b['frames'] - a['frames']) / (b['at'] - a['at'])
    since = int(time.time() - (time.monotonic() - start))
    logs = subprocess.check_output(['journalctl', '-u', 'hpr-video-mediamtx.service',
                                    '--since', '@' + str(since), '-o', 'cat', '--no-pager'], text=True)
    clock = subprocess.check_output(['timedatectl', 'show', '-p', 'NTPSynchronized',
                                     '-p', 'Timezone'], text=True)
    print(json.dumps({'duration_seconds': time.monotonic() - start,
                      'cpu_mean_percent': statistics.mean(samples),
                      'cpu_peak_percent': max(samples), 'publisher_fps': frame_rates,
                      'max_progress_age_seconds': max(gaps), 'timestamp_enabled': timestamp,
                      'reader_slow_warnings': logs.count('reader is too slow'),
                      'reader_timeout_lines': logs.count('i/o timeout'),
                      'measured_end_to_end_latency_ms': {'front': args.latency_front_ms,
                                                        'rear': args.latency_rear_ms},
                      'clock': clock.strip(), 'publisher_commands': commands}, indent=2))


if __name__ == '__main__':
    main()

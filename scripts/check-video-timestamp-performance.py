#!/usr/bin/env python3
"""Reject missing or degraded baseline/trial evidence before permanent enablement."""
import argparse
import json
from pathlib import Path


def compare(baseline, trial, accept_unmeasured_latency=False):
    errors = []
    for label, report in (('baseline', baseline), ('trial', trial)):
        if report['duration_seconds'] < 60:
            errors.append(label + ': at least 60 seconds required')
        if 'NTPSynchronized=yes' not in report['clock']:
            errors.append(label + ': clock not synchronised')
        if report['reader_slow_warnings'] or report['reader_timeout_lines']:
            errors.append(label + ': network backpressure/timeouts confound the test')
        if report['max_progress_age_seconds'] > 2:
            errors.append(label + ': stale frame progress')
    delta = trial['cpu_mean_percent'] - baseline['cpu_mean_percent']
    if delta > 5:
        errors.append(f'CPU mean increased {delta:.2f} percentage points (>5)')
    for role in ('front', 'rear'):
        if baseline['timestamp_enabled'][role] or not trial['timestamp_enabled'][role]:
            errors.append(role + ': expected timestamp OFF baseline / ON trial')
        if trial['publisher_fps'][role] < baseline['publisher_fps'][role] * 0.98:
            errors.append(role + ': frame rate reduced by more than 2%')
        a = baseline['measured_end_to_end_latency_ms'][role]
        b = trial['measured_end_to_end_latency_ms'][role]
        if any(value is not None and value < 0 for value in (a, b)):
            errors.append(role + ': invalid negative latency measurement')
        elif a is None or b is None:
            if not accept_unmeasured_latency:
                errors.append(role + ': real end-to-end latency measurements required')
        elif b - a > 40:
            errors.append(role + ': latency increased by more than 40 ms')
        # The only permitted pipeline change is inserting the overlay filter.
        from hpr_gateway.video_timestamp import GST_FILTER
        if trial['publisher_commands'][role].replace(GST_FILTER, '') != baseline['publisher_commands'][role]:
            # Socket paths differ on every restart; compare outside that token.
            import re
            clean = lambda s: re.sub(r'/run/hpr-video/hpr-video-[^/ ]+/', '/RUN/', s)
            if clean(trial['publisher_commands'][role].replace(GST_FILTER, '')) != clean(baseline['publisher_commands'][role]):
                errors.append(role + ': pipeline settings changed; invalid comparison')
    return errors, delta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline')
    parser.add_argument('trial')
    parser.add_argument('--accept-unmeasured-latency', action='store_true',
                        help='Explicit operator acceptance of missing latency evidence; never fabricates measurements')
    args = parser.parse_args()
    errors, delta = compare(json.loads(Path(args.baseline).read_text()), json.loads(Path(args.trial).read_text()),
                            args.accept_unmeasured_latency)
    print(f'CPU mean delta: {delta:+.2f} percentage points')
    if args.accept_unmeasured_latency:
        print('OPERATOR ACCEPTANCE: end-to-end latency remains UNMEASURED; no zero-overhead or latency guarantee')
    print('\n'.join(errors) if errors else 'PASS performance gate (not proof of zero overhead)')
    raise SystemExit(bool(errors))

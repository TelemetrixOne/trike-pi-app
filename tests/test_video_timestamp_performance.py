import copy
import importlib.util
from pathlib import Path
import unittest

from hpr_gateway.video_timestamp import GST_FILTER

spec = importlib.util.spec_from_file_location('timestamp_gate', Path(__file__).parents[1] / 'scripts/check-video-timestamp-performance.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class TimestampPerformanceTests(unittest.TestCase):
    def reports(self):
        baseline = {'duration_seconds': 61, 'clock': 'NTPSynchronized=yes',
                    'reader_slow_warnings': 0, 'reader_timeout_lines': 0,
                    'max_progress_age_seconds': 0.5, 'cpu_mean_percent': 30,
                    'timestamp_enabled': {'front': False, 'rear': False},
                    'publisher_fps': {'front': 30, 'rear': 25},
                    'measured_end_to_end_latency_ms': {'front': 150, 'rear': 160},
                    'publisher_commands': {'front': 'source ! x264enc', 'rear': 'source ! x264enc'}}
        trial = copy.deepcopy(baseline)
        trial['cpu_mean_percent'] = 31
        trial['timestamp_enabled'] = {'front': True, 'rear': True}
        trial['publisher_commands'] = {role: 'source ! ' + GST_FILTER + 'x264enc' for role in ('front', 'rear')}
        return baseline, trial

    def test_accepts_good_reports_and_reports_cpu_delta(self):
        self.assertEqual(gate.compare(*self.reports()), ([], 1))

    def test_rejects_unmeasured_latency_bad_clock_cpu_fps_and_network(self):
        for key, value in [('clock', 'NTPSynchronized=no'), ('cpu_mean_percent', 36),
                           ('reader_slow_warnings', 1), ('reader_timeout_lines', 1),
                           ('duration_seconds', 59), ('max_progress_age_seconds', 3),
                           ('publisher_fps', {'front': 28, 'rear': 25}),
                           ('measured_end_to_end_latency_ms', {'front': None, 'rear': 160}),
                           ('measured_end_to_end_latency_ms', {'front': 200, 'rear': 160})]:
            baseline, trial = self.reports()
            trial[key] = value
            with self.subTest(key=key, value=value):
                self.assertTrue(gate.compare(baseline, trial)[0])

    def test_rejects_changed_resolution_bitrate_or_other_pipeline_settings(self):
        baseline, trial = self.reports()
        trial['publisher_commands']['front'] += ' bitrate=2000'
        self.assertTrue(gate.compare(baseline, trial)[0])

    def test_restart_socket_paths_do_not_invalidate_comparison(self):
        baseline, trial = self.reports()
        baseline['publisher_commands']['front'] += ' /run/hpr-video/hpr-video-aaa/front.sock'
        trial['publisher_commands']['front'] += ' /run/hpr-video/hpr-video-bbb/front.sock'
        self.assertEqual(gate.compare(baseline, trial)[0], [])

    def test_explicit_acceptance_only_allows_missing_latency(self):
        baseline, trial = self.reports()
        baseline['measured_end_to_end_latency_ms'] = {'front': None, 'rear': None}
        trial['measured_end_to_end_latency_ms'] = {'front': None, 'rear': None}
        self.assertTrue(gate.compare(baseline, trial)[0])
        self.assertEqual(gate.compare(baseline, trial, accept_unmeasured_latency=True)[0], [])
        trial['cpu_mean_percent'] = 40
        self.assertTrue(gate.compare(baseline, trial, accept_unmeasured_latency=True)[0])
        baseline, trial = self.reports()
        trial['measured_end_to_end_latency_ms']['front'] = 200
        self.assertTrue(gate.compare(baseline, trial, accept_unmeasured_latency=True)[0])
        trial['measured_end_to_end_latency_ms']['front'] = -1
        self.assertTrue(gate.compare(baseline, trial, accept_unmeasured_latency=True)[0])

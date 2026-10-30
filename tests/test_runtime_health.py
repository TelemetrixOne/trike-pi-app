import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hpr_gateway import runtime_health as health


class RuntimeHealthTests(unittest.TestCase):
    def test_root_filesystem_percentage(self):
        with patch.object(health.shutil, 'disk_usage', return_value=SimpleNamespace(total=1000, used=57)) as read:
            self.assertEqual(health.disk_usage(), 5.7)
            read.assert_called_once_with('/')
        with patch.object(health.shutil, 'disk_usage', return_value=SimpleNamespace(total=0, used=0)):
            with self.assertRaises(ValueError):
                health.disk_usage()

    def test_unavailable_disk_is_null_and_does_not_hide_other_metrics(self):
        monitor = health.RuntimeHealth('trike-test')
        with patch.object(health.Path, 'read_text', self.read), \
                patch.object(health, 'disk_usage', side_effect=OSError('unavailable')), \
                patch.object(health, 'journal_counts', return_value=(0, 0)):
            monitor.collect()
        data = monitor.snapshot()
        self.assertIsNone(data['disk_used_pct'])
        self.assertIn('disk_read_failed', data['errors'])
        self.assertEqual(data['memory_used_pct'], 35)
        self.assertTrue(data['logs_complete'])

    def test_cpu_is_aggregate_not_load_or_sum_of_core_percentages(self):
        before = health.cpu_counters('cpu 100 0 100 700 100 0 0 0 50 0\ncpu0 1')
        after = health.cpu_counters('cpu 140 0 120 730 110 0 0 0 70 0')
        self.assertEqual(health.cpu_usage(before, after), 60.0)
        self.assertIsNone(health.cpu_usage(None, after))
        self.assertIsNone(health.cpu_usage(after, before))
        self.assertIsNone(health.cpu_usage(after, after))

    def test_memory_excludes_reclaimable_cache(self):
        self.assertEqual(health.memory_usage('MemTotal: 1000 kB\nMemAvailable: 650 kB\nMemFree: 100 kB'), 35)
        with self.assertRaises(ValueError):
            health.memory_usage('MemTotal: 0 kB\nMemAvailable: 0 kB')

    def test_warning_and_error_classes_are_separate(self):
        output = '\n'.join(json.dumps({'PRIORITY': str(n)}) for n in (4, 4, 3, 2, 1, 0))
        with patch.object(health.subprocess, 'run', return_value=SimpleNamespace(stdout=output, stderr='')) as run:
            self.assertEqual(health.journal_counts(), (2, 4))
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            self.assertIn('--since=-1800s', run.call_args.args[0])
            self.assertIn('--system', run.call_args.args[0])

    def test_unreadable_truncated_or_malformed_logs_never_become_zero(self):
        for output, error in (('', 'Permission denied'), ('bad json', ''),
                              ('{"PRIORITY":"4"}\n' * 3, ''), ('{"PRIORITY":"8"}', '')):
            with self.subTest(output=output), patch.object(health, 'LOG_ENTRY_LIMIT', 2), \
                    patch.object(health.subprocess, 'run', return_value=SimpleNamespace(stdout=output, stderr=error)):
                with self.assertRaises(Exception):
                    health.journal_counts()

    def test_python_warning_and_error_text_recorded_as_info_is_counted_once(self):
        rows = [({'PRIORITY': '6', 'MESSAGE': text}) for text in (
            'WARNING:hpr_gps_recovery:receiver stalled', 'ERROR:hpr_gps_recovery:failed',
            '2026-10-30 10:00:00,123 ERROR hpr_gpio: driver unavailable',
            'INFO:hpr_gps:ERROR is part of this informational message', 'ordinary informational log')]
        rows.append({'PRIORITY': '3', 'MESSAGE': 'ERROR:already journal error'})
        with patch.object(health.subprocess, 'run', return_value=SimpleNamespace(
                stdout='\n'.join(json.dumps(row) for row in rows), stderr='')):
            self.assertEqual(health.journal_counts(), (1, 3))

    @staticmethod
    def read(path):
        return {'/proc/stat': 'cpu 100 0 100 700 100 0 0 0',
                '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 650 kB',
                '/sys/class/thermal/thermal_zone0/temp': '28700'}[path.as_posix()]

    def test_logs_cached_and_snapshot_has_timestamp_units_and_identity(self):
        monitor = health.RuntimeHealth('configured-trike')
        with patch.object(health.Path, 'read_text', self.read), \
                patch.object(health, 'journal_counts', return_value=(2, 3)) as logs:
            monitor.collect()
            monitor.collect()
        self.assertEqual(logs.call_count, 1)
        data = monitor.snapshot()
        self.assertEqual(data['trike_id'], 'configured-trike')
        self.assertEqual(data['memory_used_pct'], 35)
        self.assertEqual(data['cpu_temperature_c'], 28.7)
        self.assertEqual(data['warnings_count'], 2)
        self.assertEqual(data['errors_count'], 3)
        self.assertTrue(data['logs_complete'])
        self.assertIsNotNone(data['logs_observed_at'])
        self.assertIsNotNone(data['observed_at'])

    def test_failures_publish_null_without_crashing_service(self):
        monitor = health.RuntimeHealth('trike-test')
        with patch.object(health.Path, 'read_text', side_effect=OSError('missing')), \
                patch.object(health, 'journal_counts', side_effect=subprocess.TimeoutExpired('journalctl', 5)):
            monitor.collect()
        data = monitor.snapshot()
        self.assertIsNone(data['cpu_usage_pct'])
        self.assertIsNone(data['memory_used_pct'])
        self.assertIsNone(data['cpu_temperature_c'])
        self.assertIsNone(data['warnings_count'])
        self.assertFalse(data['logs_complete'])
        self.assertEqual(len(data['errors']), 4)

    def test_cached_logs_refresh_and_failure_does_not_keep_old_counts(self):
        monitor = health.RuntimeHealth('trike-test')
        with patch.object(health.Path, 'read_text', self.read), \
                patch.object(health.time, 'monotonic', side_effect=[100, 159, 160]), \
                patch.object(health, 'journal_counts', side_effect=[(2, 3), OSError('unavailable')]) as logs:
            monitor.collect()
            monitor.collect()
            self.assertEqual(logs.call_count, 1)
            monitor.collect()
        self.assertEqual(logs.call_count, 2)
        self.assertIsNone(monitor.snapshot()['errors_count'])
        self.assertFalse(monitor.snapshot()['logs_complete'])

    def test_journal_success_with_no_entries_is_zero(self):
        with patch.object(health.subprocess, 'run', return_value=SimpleNamespace(stdout='', stderr='')):
            self.assertEqual(health.journal_counts(), (0, 0))

    def test_slow_journal_query_does_not_block_publisher_snapshot(self):
        monitor = health.RuntimeHealth('trike-test')
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return 0, 0
        with patch.object(health.Path, 'read_text', self.read), patch.object(health, 'journal_counts', slow):
            monitor.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertIsNone(monitor.snapshot()['observed_at'])
                self.assertFalse(release.is_set())
            finally:
                release.set()
                monitor.stop()


if __name__ == '__main__':
    unittest.main()

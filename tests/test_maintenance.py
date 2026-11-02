"""Offline admission tests; never contact central or a Pi."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hpr_gateway import maintenance as guard


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.identity = {'trike_id': 'test-trike', 'topic_root': 'test-root'}
        self.value = {'schema_version': 1, **self.identity, 'enabled': True, 'uses_pi': True,
                      'server_time_utc': self.now.isoformat(), 'blocked': False,
                      'phase': 'ended', 'session_id': 'test-session'}

    def decide(self, **updates):
        return guard.decision({**self.value, **updates}, self.identity, None, self.now)

    def test_only_explicit_idle_admits(self):
        for phase in ('ended', 'draft'):
            self.assertTrue(self.decide(phase=phase)[1])
        for phase in ('active', 'armed', 'scheduled', 'disabled', 'unknown', '', None):
            with self.subTest(phase=phase):
                evidence, allowed = self.decide(phase=phase)
                self.assertFalse(allowed); self.assertTrue(evidence['latched'])

    def test_blocked_overrides_idle(self):
        self.assertFalse(self.decide(blocked=True)[1])

    def test_invalid_identity_schema_capability_and_decision(self):
        for update in ({'trike_id': 'wrong'}, {'topic_root': 'wrong'}, {'enabled': False},
                       {'enabled': 1}, {'uses_pi': False}, {'blocked': None}, {'blocked': 0},
                       {'schema_version': True}, {'schema_version': 2}):
            with self.subTest(update=update), self.assertRaises(guard.MaintenanceBlocked):
                self.decide(**update)

    def test_stale_future_missing_naive_and_malformed_time(self):
        for stamp in (None, 'bad', self.now.replace(tzinfo=None).isoformat(),
                      (self.now-timedelta(seconds=16)).isoformat(),
                      (self.now+timedelta(seconds=16)).isoformat()):
            with self.subTest(stamp=stamp), self.assertRaises(guard.MaintenanceBlocked):
                self.decide(server_time_utc=stamp)

    def test_active_latch_survives_reload_until_newer_idle(self):
        previous, _ = self.decide(phase='active', blocked=True)
        previous = json.loads(json.dumps(previous))  # durable/reboot round trip
        self.assertFalse(guard.decision(self.value, self.identity, previous, self.now)[1])
        newer = {**self.value, 'server_time_utc': (self.now+timedelta(seconds=1)).isoformat()}
        self.assertTrue(guard.decision(newer, self.identity, previous, self.now)[1])
        older = {**self.value, 'server_time_utc': (self.now-timedelta(seconds=1)).isoformat()}
        with self.assertRaises(guard.MaintenanceBlocked):
            guard.decision(older, self.identity, previous, self.now)

    def test_unavailable_does_not_clear_durable_latch(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); path = state/'protection.json'
            path.write_text(json.dumps(self.decide(phase='active', blocked=True)[0]))
            before = path.read_bytes()
            with patch.object(guard, 'STATE', state), patch.object(guard, 'trusted_config', return_value=(self.identity, 'https://test.invalid/state')), patch.object(guard, 'fetch', side_effect=TimeoutError):
                with self.assertRaises(guard.MaintenanceBlocked): guard.check()
            self.assertEqual(before, path.read_bytes())

    def test_denied_main_never_runs(self):
        called = []
        @contextmanager
        def refuse():
            raise guard.MaintenanceBlocked('active')
            yield
        with patch.object(guard, 'admission', refuse):
            self.assertEqual(75, guard.guarded_main(lambda: called.append('mutation')))
        self.assertEqual([], called)

    def test_redirects_refuse(self):
        with self.assertRaises(guard.MaintenanceBlocked):
            guard.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_mutation_and_process_boundaries_recheck(self):
        with patch.object(guard, '_boundary_enabled', True), patch.object(guard, 'check') as check:
            for event, args in (('open', ('path', 'w', os.O_WRONLY)), ('os.rename', ()),
                                ('os.mkdir', ()), ('subprocess.Popen', ())):
                guard._audit(event, args)
            self.assertEqual(4, check.call_count)
            guard._audit('open', ('path', 'r', os.O_RDONLY))
            self.assertEqual(4, check.call_count)
            guard.recheck_if_guarded()
            self.assertEqual(5, check.call_count)

    def test_guard_bookkeeping_does_not_recurse(self):
        with patch.object(guard, '_boundary_enabled', True), patch.object(guard, 'check') as check:
            guard._inside.checking = True
            try: guard._audit('os.rename', ())
            finally: guard._inside.checking = False
            check.assert_not_called()

    def test_all_operator_scripts_guard_before_work(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root/'scripts').glob('*.sh'):
            if not path.name.startswith(('deploy-', 'update-', 'rollback-', 'install-', 'rebuild-', 'apply-', 'restore-')) and path.name != 'video-timestamp-mode.sh':
                continue
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertIn('pi-maintenance-guard.sh', text)
                lines = [x for x in text.splitlines() if x and not x.startswith('#')]
                self.assertTrue(lines[0].startswith('set -'))
                self.assertTrue(lines[1].startswith('source '))
        for path in (root/'scripts').glob('*.py'):
            if path.name.startswith(('deploy-', 'activate-', 'prepare-', 'reload-', 'remove-', 'update-')) or path.name == 'test-pi-media-broker.py':
                self.assertIn('raise SystemExit(guarded_main(main))', path.read_text(), path.name)

    def test_runtime_services_do_not_import_operator_guard(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root/'hpr_gateway/services').glob('*.py'):
            self.assertNotIn('hpr_gateway.maintenance', path.read_text(), path.name)

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root flock integration')
    def test_real_flock_nested_children_and_rechecks(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); os.chmod(state, 0o700)
            with patch.object(guard, 'STATE', state), patch.object(guard, 'check') as check:
                def operation():
                    with guard.admission(): pass  # same inherited open description
                    code = 'import fcntl;fcntl.flock(199,fcntl.LOCK_EX|fcntl.LOCK_NB);print("nested-ok")'
                    result = subprocess.check_output([sys.executable, '-c', code], text=True)
                    self.assertIn('nested-ok', result)
                    competitor = 'import fcntl;f=open('+repr(str(state/'operation.lock'))+',"a");fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                    result = subprocess.run([sys.executable, '-c', competitor], capture_output=True)
                    self.assertNotEqual(0, result.returncode)
                    return 0
                self.assertEqual(0, guard.guarded_main(operation))
                self.assertGreaterEqual(check.call_count, 4)
            with (state/'operation.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released afterwards

    def test_cancellable_isolated_job_stops_when_protected(self):
        from unittest.mock import MagicMock
        process = MagicMock(); process.pid = 123; process.poll.return_value = None
        with patch.object(guard.subprocess, 'Popen') as popen, patch.object(guard, 'check', side_effect=guard.MaintenanceBlocked('active')), patch.object(os, 'killpg', create=True) as kill:
            popen.return_value.__enter__.return_value = process
            with self.assertRaises(guard.MaintenanceBlocked): guard.cancellable_run(['isolated-job'])
            self.assertEqual(123, kill.call_args.args[0])

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root shell integration')
    def test_shell_blocks_before_work_and_rechecks_function_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        for admit_first in (False, True):
            with self.subTest(admit_first=admit_first), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory); marker = fixture/'mutated'; counter = fixture/'calls'
                source = (root/'scripts/pi-maintenance-guard.sh').read_text()
                (fixture/'pi-maintenance-guard.sh').write_text(source.replace('/var/lib/hpr/maintenance', str(fixture/'state')))
                helper = 'from pathlib import Path\nimport sys\np=Path('+repr(str(counter))+')\nn=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\nsys.exit(0 if '+repr(admit_first)+' and n==0 else 75)\n'
                (fixture/'pi-maintenance-check.py').write_text(helper)
                script = 'set -eu\nsource '+repr(str(fixture/'pi-maintenance-guard.sh'))+'\nf() { systemctl() { touch '+repr(str(marker))+'; }; systemctl restart mock; }\nf\n'
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=15)
                self.assertEqual(75, result.returncode, result.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual(2 if admit_first else 1, int(counter.read_text()))

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root shell integration')
    def test_shell_nested_scripts_share_lock_and_can_finish_idle(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory); marker = fixture/'finished'
            source = (root/'scripts/pi-maintenance-guard.sh').read_text()
            (fixture/'pi-maintenance-guard.sh').write_text(source.replace('/var/lib/hpr/maintenance', str(fixture/'state')))
            (fixture/'pi-maintenance-check.py').write_text('raise SystemExit(0)\n')
            child = fixture/'child.sh'
            child.write_text('set -eu\nsource '+repr(str(fixture/'pi-maintenance-guard.sh'))+'\ntouch '+repr(str(marker))+'\n')
            script = 'set -eu\nsource '+repr(str(fixture/'pi-maintenance-guard.sh'))+'\nbash '+repr(str(child))+'\n'
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(marker.exists())


if __name__ == '__main__': unittest.main()

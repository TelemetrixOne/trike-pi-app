import importlib.util
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('stream_trial', Path(__file__).parents[1] / 'bin/hpr-video-stream-trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class VideoStreamTrialTests(unittest.TestCase):
    def setUp(self):
        self.original = {'cameras': {'front': {'stream_size': '640x360', 'bitrate': '700k',
                         'rotation': '180', 'output_framerate': 25, 'path': 'front-camera'},
                         'rear': {'stream_size': '640x360', 'bitrate': '700k'}},
                         'display': {'capture_size': '1280x720', 'capture_framerate': 30},
                         'rtsp_port': 8554}

    def test_only_front_resolution_and_bitrate_change(self):
        updated = trial.trial_settings(self.original, {'stream_size': '800x450', 'bitrate': '1500k'})
        self.assertEqual(self.original['cameras']['front']['stream_size'], '640x360')
        self.assertEqual(updated['cameras']['rear'], self.original['cameras']['rear'])
        self.assertEqual(updated['display'], self.original['display'])
        self.assertEqual(updated['cameras']['front']['output_framerate'], 25)
        self.assertEqual(updated['cameras']['front']['rotation'], '180')
        self.assertEqual(updated['cameras']['front']['path'], 'front-camera')
        self.assertEqual(updated['cameras']['front']['stream_size'], '800x450')
        self.assertEqual(updated['cameras']['front']['bitrate'], '1500k')

    def test_rejects_arbitrary_overrides_distortion_and_unbounded_bitrate(self):
        for settings in ({'device': '/dev/video0'}, {'stream_size': '800x600', 'bitrate': '1500k'},
                         {'stream_size': '800x450', 'bitrate': '0k'},
                         {'stream_size': '800x450', 'bitrate': '90000k'},
                         {'stream_size': '800x450', 'bitrate': 'shell;command'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                trial.trial_settings(self.original, settings)

    def test_centrally_disabled_front_is_not_enabled(self):
        self.original['cameras']['front']['enabled'] = False
        with self.assertRaises(ValueError):
            trial.trial_settings(self.original, {'stream_size': '800x450', 'bitrate': '1500k'})

    def test_missing_trial_falls_back_to_current_central_video(self):
        with patch.object(trial, 'central_video', return_value=self.original), \
                patch.object(trial.Path, 'read_text', side_effect=FileNotFoundError), \
                patch.object(trial.os, 'execve') as execute:
            trial.run()
        import json
        self.assertEqual(json.loads(execute.call_args.args[2]['HPR_VIDEO_SETTINGS']), self.original)

    def test_stop_refuses_unidentified_dropin(self):
        with patch.object(trial.Path, 'exists', return_value=True), \
                patch.object(trial.Path, 'read_text', return_value='unidentified enhancement'), \
                patch.object(trial, 'systemctl') as control:
            with self.assertRaises(RuntimeError):
                trial.stop()
            control.assert_not_called()

    def test_stop_restores_service_and_keeps_other_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'video-stream-trial-example'
            backup.mkdir()
            state, dropin, other = root / 'trial.json', root / '90-stream-trial.conf', root / 'other.conf'
            state.write_text(json.dumps({'backup': str(backup)}))
            dropin.write_text(trial.DROPIN_TEXT)
            other.write_text('unrelated enhancement')
            with patch.object(trial, 'STATE', state), patch.object(trial, 'DROPIN', dropin), \
                    patch.object(trial, 'BACKUP_ROOT', root), \
                    patch.object(trial, 'central_video', return_value=self.original), \
                    patch.object(trial, 'systemctl', return_value='active') as control:
                trial.stop()
                trial.stop()  # Already stopped: do not restart anything again.
            self.assertFalse(dropin.exists())
            self.assertFalse(state.exists())
            self.assertEqual(other.read_text(), 'unrelated enhancement')
            self.assertTrue((backup / 'trial-before-stop.json').exists())
            self.assertEqual(control.call_args_list.count(unittest.mock.call('restart', trial.UNIT)), 1)

    def test_failed_start_automatically_removes_override_and_restarts_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, dropin = root / 'state/trial.json', root / 'dropins/trial.conf'
            with patch.object(trial, 'STATE', state), patch.object(trial, 'DROPIN', dropin), \
                    patch.object(trial, 'BACKUP_ROOT', root), \
                    patch.object(trial, 'central_video', return_value=self.original), \
                    patch.object(trial, 'read_status', return_value={'children': {'front-publish': {}, 'rear-publish': {}}}), \
                    patch.object(trial, 'systemctl', return_value='active') as control, \
                    patch.object(trial, 'verify', side_effect=RuntimeError('deliberate failed trial')), \
                    patch.object(trial.shutil, 'copy2'), patch.object(trial.os, 'chown', create=True), \
                    patch.dict('sys.modules', {'grp': SimpleNamespace(getgrnam=lambda _: SimpleNamespace(gr_gid=0))}):
                with self.assertRaisesRegex(RuntimeError, 'deliberate failed trial'):
                    trial.start('800x450', '1500k')
            self.assertFalse(dropin.exists())
            self.assertFalse(state.exists())
            self.assertEqual(control.call_args_list.count(unittest.mock.call('restart', trial.UNIT)), 2)


if __name__ == '__main__':
    unittest.main()

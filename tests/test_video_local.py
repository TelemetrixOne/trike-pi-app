import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hpr_gateway import video_local as local


class VideoLocalTests(unittest.TestCase):
    def setUp(self):
        self.video = {'cameras': {'front': {'stream_size': '640x360', 'bitrate': '700k',
                      'output_framerate': 25, 'rotation': '180', 'device': 'central-front',
                      'path': 'central-path'}, 'rear': {'stream_size': '640x360', 'bitrate': '700k'}},
                      'display': {'capture_size': '1280x720', 'capture_framerate': 30}, 'rtsp_port': 8554}

    def test_only_quality_changes_and_input_is_not_mutated(self):
        before = copy.deepcopy(self.video)
        overlay = {'front': {'stream_size': '800x450', 'output_framerate': 25, 'bitrate': '1500k'}}
        effective = local.merge_local(self.video, overlay)
        self.assertEqual(self.video, before)
        self.assertEqual(effective['cameras']['front'], {**before['cameras']['front'], **overlay['front']})
        self.assertEqual(effective['cameras']['rear'], before['cameras']['rear'])
        self.assertEqual(effective['display'], before['display'])
        self.assertEqual(effective['rtsp_port'], before['rtsp_port'])

    def test_rejects_identity_overrides_wrong_types_sizes_rates_and_bitrates(self):
        bad = [[], {'other': {}}, {'front': []}, {'front': {'device': 'different'}},
               {'front': {'rotation': 'none'}}, {'front': {'stream_size': '801x601'}},
               {'front': {'capture_size': '801x601'}},
               {'front': {'output_framerate': True}}, {'front': {'output_framerate': 31}},
               {'front': {'bitrate': '0k'}}, {'front': {'bitrate': '100M'}},
               {'front': {'bitrate': 'shell;command'}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                local.merge_local(self.video, value)

    def test_does_not_enable_centrally_disabled_camera(self):
        self.video['cameras']['front']['enabled'] = False
        effective = local.merge_local(self.video, {'front': {'bitrate': '1.5M'}})
        self.assertFalse(effective['cameras']['front']['enabled'])

    def test_missing_local_file_uses_central(self):
        with patch.object(local, 'load_config', return_value={'video': self.video}), \
                patch.object(local, 'read_local', side_effect=FileNotFoundError):
            self.assertEqual(local.video_settings('central'), self.video)

    def test_invalid_local_file_falls_back_without_partial_application(self):
        with patch.object(local, 'load_config', return_value={'video': self.video}), \
                patch.object(local, 'read_local', return_value={'front': {'bitrate': '1500k'}, 'rear': {'path': 'bad'}}), \
                self.assertLogs(local.LOG, level='ERROR'):
            self.assertEqual(local.video_settings('central'), self.video)

    def test_duplicate_and_oversize_yaml_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.yaml'
            for value in ('front: {bitrate: 700k, bitrate: 1500k}', '#' * 8193):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    local.read_local(str(path))

    def test_shipped_profiles(self):
        root = Path(__file__).parents[1] / 'config'
        self.assertEqual(local.merge_local(self.video, local.read_local(str(root / 'video-local.example.yaml'))), self.video)
        effective = local.merge_local(self.video, local.read_local(str(root / 'video-local.front-800x450.yaml')))
        self.assertEqual(effective['cameras']['front']['stream_size'], '800x450')
        self.assertEqual(effective['cameras']['front']['bitrate'], '1500k')
        effective = local.merge_local(self.video, local.read_local(str(root / 'video-local.front-1280x720.yaml')))
        self.assertEqual(effective['cameras']['front']['stream_size'], '1280x720')
        self.assertEqual(effective['cameras']['front']['output_framerate'], 30)
        self.assertEqual(effective['cameras']['front']['bitrate'], '2500k')
        self.assertEqual(effective['cameras']['rear'], self.video['cameras']['rear'])
        effective = local.merge_local(self.video, local.read_local(str(root / 'video-local.front-800x600.yaml')))
        self.assertEqual(effective['cameras']['front']['capture_size'], '1280x720')
        self.assertEqual(effective['cameras']['front']['stream_size'], '800x600')
        self.assertEqual(effective['cameras']['front']['bitrate'], '2500k')
        self.assertEqual(effective['cameras']['rear'], self.video['cameras']['rear'])
        self.assertEqual(effective['display'], self.video['display'])

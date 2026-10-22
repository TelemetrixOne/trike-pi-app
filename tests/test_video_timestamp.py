import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest import TestCase
from unittest.mock import patch

from hpr_gateway.video_timestamp import environment, FORMAT, FF_FILTER, GST_FILTER


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class VideoTimestampTests(TestCase):
    def test_disabled_is_no_filter_and_trial_override(self):
        off = environment(Config(), {'TZ': 'Australia/Sydney'})
        self.assertEqual(off['HPR_VIDEO_GST_TIMESTAMP'], '')
        self.assertEqual(off['HPR_VIDEO_FF_TIMESTAMP'], '')
        on = environment(Config(), {'TZ': 'Australia/Sydney', 'HPR_VIDEO_TIMESTAMP_ENABLED': '1'})
        self.assertIn(FORMAT, on['HPR_VIDEO_GST_TIMESTAMP'])
        self.assertEqual(on['TZ'], 'Australia/Sydney')
        self.assertEqual(environment(Config(**{'video.timestamp.enabled': True}),
                                     {'TZ': 'Australia/Sydney', 'HPR_VIDEO_TIMESTAMP_ENABLED': '0'})
                         ['HPR_VIDEO_GST_TIMESTAMP'], '')

    def test_central_race_timezone_precedence_and_validation(self):
        cfg = Config(**{'race.timezone': 'Australia/Sydney', 'video.timestamp.enabled': True})
        self.assertEqual(environment(cfg, {'TZ': 'UTC'})['TZ'], 'Australia/Sydney')
        for cfg, env in [(Config(**{'video.timestamp.enabled': 'false'}), {}),
                         (Config(**{'video.timestamp.timezone': '../bad'}), {}),
                         (Config(), {'HPR_VIDEO_TIMESTAMP_ENABLED': 'yes'})]:
            with self.assertRaises((ValueError, KeyError)):
                environment(cfg, env)

    def test_sydney_summer_winter_and_exact_format(self):
        zone = ZoneInfo('Australia/Sydney')
        summer = datetime(2026, 10, 23, tzinfo=timezone.utc).astimezone(zone)
        winter = datetime(2026, 7, 23, tzinfo=timezone.utc).astimezone(zone)
        self.assertEqual(summer.strftime(FORMAT), '11:00:00 23-10-2026')
        self.assertEqual(winter.strftime(FORMAT), '10:00:00 23-07-2026')

    def test_native_both_streams_one_encode_and_hdmi_unchanged(self):
        spec = importlib.util.spec_from_file_location('timestamp_video', Path(__file__).parents[1] / 'bin/hpr-video-native.py')
        video = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(video)
        with patch.dict(os.environ, {'HPR_VIDEO_GST_TIMESTAMP': GST_FILTER}):
            for role in ('front', 'rear'):
                pipeline = video.publisher_pipeline('/tmp/' + role, '1280x720', 30,
                                                    {'stream_size': '800x450'}, role, 8554)
                self.assertEqual(pipeline.count('x264enc '), 1)
                self.assertEqual(pipeline.count('clockoverlay '), 1)
                self.assertLess(pipeline.index('clockoverlay '), pipeline.index('x264enc '))
                self.assertGreater(pipeline.index('clockoverlay '), pipeline.index('videoscale '))
                self.assertNotIn('clockoverlay', video.display_pipeline('/tmp/front', '1280x720', 30,
                                                                      {}, (33, 800, 480)))
        self.assertIn('silent=true', GST_FILTER)
        with patch.object(video.subprocess, 'check_output', return_value='yes\n'):
            self.assertTrue(video.clock_synchronised())
        for output in ('no\n', ''):
            with patch.object(video.subprocess, 'check_output', return_value=output):
                self.assertFalse(video.clock_synchronised())
        with patch.object(video.subprocess, 'check_output', side_effect=OSError):
            self.assertFalse(video.clock_synchronised())

    def test_legacy_all_network_branches_only(self):
        root = Path(__file__).parents[1] / 'bin'
        compose = (root / 'hpr-video-compose.sh').read_text()
        network = [line for line in compose.splitlines() if 'setsar=1' in line and ('[mn]' in line or '[pn]' in line)]
        self.assertEqual(len(network), 6)
        for line in network:
            self.assertIn('setsar=1${HPR_VIDEO_FF_TIMESTAMP}', line)
            if '[display]' in line:
                self.assertNotIn('HPR_VIDEO_FF_TIMESTAMP', line.split('[md0]')[-1] if '[md0]${MR}' in line
                                 else line.split('[pd0]')[-1])
        publish = (root / 'hpr-video-publish.sh').read_text()
        self.assertEqual(publish.count('setsar=1${HPR_VIDEO_FF_TIMESTAMP}'), 2)

    def test_ffmpeg_filter_actually_parses(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg not installed')
        result = subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                                 'color=c=blue:s=640x360:r=25', '-vf',
                                 FF_FILTER.replace("font='DejaVu Sans'", "fontfile='/Windows/Fonts/arial.ttf'")
                                 if os.name == 'nt' else FF_FILTER,
                                 '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertNotIn(b'not known', result.stderr)
        # Parsing alone is insufficient: unsupported strftime formats can
        # silently render an empty string. Require actual white glyph pixels.
        pixels = result.stdout[-640 * 40 * 3:]
        white = sum(min(pixels[i:i+3]) > 200 for i in range(0, len(pixels), 3))
        self.assertGreater(white, 100, 'Timestamp rendered blank')

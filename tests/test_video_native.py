import importlib.util
from pathlib import Path
import tempfile
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('video_native', Path(__file__).parents[1] / 'bin/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)


class NativeVideoTests(unittest.TestCase):
    def test_watchdog_detects_live_process_without_frame_progress(self):
        self.assertFalse(video.stalled(100, {}, 110))
        self.assertTrue(video.stalled(100, {}, 116))
        self.assertFalse(video.stalled(100, {'at': 119}, 120))
        self.assertTrue(video.stalled(100, {'at': 111}, 120))
        self.assertTrue(video.stalled(100, {'at': 102}, 105))

    def test_camera_move_preserves_identifiable_rear(self):
        with tempfile.TemporaryDirectory() as directory:
            rear, moved = Path(directory) / 'rear', Path(directory) / 'moved'
            rear.touch()
            moved.touch()
            cameras = {'front': {'device': directory + '/absent'}, 'rear': {'device': str(rear)}}
            self.assertEqual(video.resolve_devices(cameras, [str(rear), str(moved)]),
                             {'front': str(moved), 'rear': str(rear)})
            cameras['front']['enabled'] = False
            self.assertEqual(video.resolve_devices(cameras, [str(moved), str(rear)]), {'rear': str(rear)})

    def test_rear_only_does_not_get_assigned_to_front(self):
        with tempfile.NamedTemporaryFile() as rear:
            cameras = {'front': {'device': '/absent'}, 'rear': {'device': rear.name}}
            self.assertEqual(video.resolve_devices(cameras, [rear.name]), {'rear': rear.name})

    def test_mode_belongs_to_connected_connector(self):
        text = ('Connector 0 (33) HDMI-A-1 (disconnected)\n'
                '  Crtc 0 (90) 1920x1080@60\n'
                'Connector 1 (42) HDMI-A-2 (connected)\n'
                '  Crtc 3 (100) 800x480@60\n')
        with patch.object(video.subprocess, 'check_output', return_value=text):
            self.assertEqual(video.display_mode(), (42, 800, 480))

    def test_hdmi_is_uncropped_and_publisher_keeps_central_settings(self):
        camera = {'rotation': '180', 'stream_size': '640x360', 'path': 'race-front',
                  'bitrate': '900k', 'gop': 25, 'output_framerate': 25}
        display = video.display_pipeline('/tmp/front.sock', '1280x720', 30, camera, (33, 800, 480))
        self.assertIn('video-direction=180', display)
        self.assertIn('add-borders=true', display)
        self.assertNotIn('crop', display)
        self.assertNotIn('compositor', display)
        publisher = video.publisher_pipeline('/tmp/front.sock', '1280x720', 30, camera, 'front', 9554)
        self.assertIn('bitrate=900', publisher)
        self.assertIn('127.0.0.1:9554/race-front', publisher)
        self.assertIn('width=640,height=360,framerate=25/1', publisher)

    def test_hdmi_hotplug_does_not_restart_publishers(self):
        started, handlers, loops = [], {}, [0]

        class FakeChild:
            def __init__(self, name, kind, description, directory):
                started.append(name)
                self.description = description
                self.process = SimpleNamespace(pid=len(started))
                self.heartbeat = directory / (name + '.json')
                for path in video.re.findall(r'socket-path=("[^"]+")', description):
                    Path(video.json.loads(path)).touch()

            def unhealthy(self):
                return False

            def stop(self):
                pass

        def tick(seconds):
            loops[0] += 1
            if loops[0] == 3:
                handlers[signal.SIGTERM]()

        with tempfile.TemporaryDirectory() as directory:
            front, rear = Path(directory) / 'front', Path(directory) / 'rear'
            front.touch()
            rear.touch()
            settings = {'cameras': {'front': {'device': str(front)}, 'rear': {'device': str(rear)}}}
            with patch.dict(video.os.environ, {'RUNTIME_DIRECTORY': directory}), \
                 patch.object(video, 'Child', FakeChild), \
                 patch.object(video.signal, 'signal', side_effect=lambda sig, handler: handlers.update({sig: handler})), \
                 patch.object(video.time, 'sleep', side_effect=tick), \
                 patch.object(video, 'display_mode', side_effect=[None, (33, 800, 480), None]):
                video.supervise(settings)
        self.assertEqual(started.count('front-publish'), 1)
        self.assertEqual(started.count('rear-publish'), 1)
        self.assertEqual(started.count('hdmi'), 1)


if __name__ == '__main__':
    unittest.main()

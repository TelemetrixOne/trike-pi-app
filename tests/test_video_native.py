import importlib.util
from pathlib import Path
import tempfile
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

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

    def test_publisher_preserves_rotation_quality_and_full_frame(self):
        camera = {'rotation': '180', 'stream_size': '640x360', 'path': 'race-front',
                  'bitrate': '900k', 'gop': 25, 'output_framerate': 25}
        publisher = video.publisher_pipeline('/tmp/front.sock', '1280x720', 30, camera, 'front', 9554)
        self.assertIn('video-direction=180', publisher)
        self.assertIn('add-borders=true', publisher)
        self.assertNotIn('crop', publisher)
        self.assertIn('bitrate=900', publisher)
        self.assertIn('127.0.0.1:9554/race-front', publisher)
        self.assertIn('width=640,height=360,framerate=25/1', publisher)

    def test_matching_native_and_stream_modes_skip_scaling(self):
        pipeline = video.publisher_pipeline('/tmp/front', '1280x720', 30,
                                           {'rotation': '180', 'stream_size': '1280x720'}, 'front', 8554)
        self.assertNotIn('videoscale', pipeline)
        self.assertIn('video-direction=180', pipeline)
        self.assertIn('x264enc', pipeline)

    def test_720p_source_fits_800x600_stream_without_crop_or_stretch(self):
        camera = {'rotation': '180', 'stream_size': '800x600',
                  'output_framerate': 30, 'bitrate': '2500k'}
        publisher = video.publisher_pipeline('/tmp/front.sock', '1280x720', 30, camera, 'front', 8554)
        self.assertIn('image/jpeg,width=1280,height=720,', publisher)
        self.assertIn('width=800,height=600,framerate=30/1', publisher)
        self.assertIn('videoscale add-borders=true', publisher)
        self.assertIn('format=I420,pixel-aspect-ratio=1/1', publisher)
        self.assertNotIn('crop', publisher)
    def test_stream_only_supervisor_preserves_per_camera_capture(self):
        started, handlers, loops, pipelines = [], {}, [0], {}

        class FakeChild:
            def __init__(self, name, kind, description, directory):
                started.append(name)
                pipelines[name] = description
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
            settings = {'display': {'enabled': True, 'picture_in_picture': {'enabled': True}}, 'cameras': {'front': {'device': str(front), 'capture_size': '800x600', 'capture_framerate': 25,
                                              'stream_size': '800x600'},
                                     'rear': {'device': str(rear)}}}
            with patch.dict(video.os.environ, {'RUNTIME_DIRECTORY': directory}), \
                 patch.object(video, 'CameraControls', return_value=MagicMock(snapshot=lambda: {})), \
                 patch.object(video, 'Child', FakeChild), \
                 patch.object(video.signal, 'signal', side_effect=lambda sig, handler: handlers.update({sig: handler})), \
                 patch.object(video.time, 'sleep', side_effect=tick):
                video.supervise(settings)
        self.assertEqual(started.count('front-publish'), 1)
        self.assertEqual(started.count('rear-publish'), 1)
        self.assertEqual(set(started), {'front-capture', 'front-publish', 'rear-capture', 'rear-publish'})
        for name in ('front-capture', 'front-publish'):
            self.assertIn('image/jpeg,width=800,height=600,', pipelines[name])
        for name in ('rear-capture', 'rear-publish'):
            self.assertIn('image/jpeg,width=1280,height=720,', pipelines[name])
        self.assertIn('framerate=25/1', pipelines['front-capture'])
        self.assertIn('framerate=30/1', pipelines['rear-capture'])
        self.assertNotIn('display.sock', str(pipelines))
        self.assertNotIn('tee ', pipelines['front-capture'])


if __name__ == '__main__':
    unittest.main()

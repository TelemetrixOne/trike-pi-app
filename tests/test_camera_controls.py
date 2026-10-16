import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('camera_video', Path(__file__).parents[1] / 'bin/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)


class CameraControlsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'controls.yaml'
        self.manager = video.CameraControls(self.path)
        self.values = {'brightness': 0, 'contrast': 32, 'auto_exposure': 3,
                       'white_balance_automatic': 1}
        self.writes = []
        self.identity = ('/dev/video0', 123, 456)
        self.manager.identity = lambda device: self.identity
        self.manager.command = self.command

    def command(self, device, arg):
        if arg.startswith('--set-ctrl='):
            name, value = arg.split('=', 1)[1].split('=')
            self.writes.append((name, int(value)))
            self.values[name] = int(value)
            return ''
        return (f" brightness 0x00980900 (int) : min=-128 max=127 step=1 value={self.values['brightness']}\n"
                f" contrast 0x00980901 (int) : min=0 max=64 step=1 value={self.values['contrast']}\n"
                f" auto_exposure 0x009a0901 (menu) : min=0 max=3 value={self.values['auto_exposure']}\n"
                "  1: Manual Mode\n  3: Aperture Priority Mode\n"
                f" white_balance_automatic 0x0098090c (bool) : default=1 value={self.values['white_balance_automatic']}\n")

    def profile(self, text='front:\n  brightness: 10\nrear:\n  contrast: 40\n'):
        self.path.write_text(text)

    def targets(self):
        self.manager.update_devices({'front': ('/dev/video0', self.identity),
                                     'rear': ('/dev/video1', self.identity)})

    def test_reject_invalid_files(self):
        for text in ('front: [1]', 'device: /dev/video0', 'front: {brightness: true}',
                     'front: {brightness: 2.5}', 'front: {rotate: 180}',
                     'front: {exposure_time_absolute: 100}',
                     'rear: {white_balance_temperature: 4000}', 'front: [',
                     'front: {brightness: 1, brightness: 2}'):
            with self.subTest(text=text):
                self.profile(text)
                with self.assertRaises(Exception):
                    video.load_picture_controls(self.path)

    def test_example_loads(self):
        data = video.load_picture_controls(Path(__file__).parents[1] / 'config/camera-controls.example.yaml')
        self.assertEqual(data['front'], data['rear'])
        self.assertEqual(data['front']['auto_exposure'], 3)

    def test_sunny_profile_preserves_automatic_exposure_and_minimum_gain(self):
        data = video.load_picture_controls(Path(__file__).parents[1] / 'config/camera-controls.sunny.example.yaml')
        self.assertEqual(data['front'], data['rear'])
        self.assertEqual(data['front']['auto_exposure'], 3)
        self.assertNotIn('exposure_time_absolute', data['front'])
        self.assertEqual(data['front']['gamma'], 60)
        self.assertEqual(data['front']['gain'], 0)

    def test_menu_and_boolean_capabilities(self):
        caps = video.parse_picture_capabilities(self.command('', '--list-ctrls-menus'))
        self.assertEqual(caps['auto_exposure']['choices'], [1, 3])
        self.assertEqual(caps['white_balance_automatic']['min'], 0)
        self.assertEqual(caps['white_balance_automatic']['max'], 1)

    def test_complete_validation_precedes_writes(self):
        for desired in ({'brightness': 10, 'contrast': 65}, {'brightness': 10, 'gain': 4},
                        {'brightness': 10, 'auto_exposure': 2}):
            with self.assertRaises(ValueError):
                self.manager.apply('/dev/video0', self.identity, desired)
        self.assertEqual(self.writes, [])

    def test_unchanged_profile_does_not_write(self):
        self.manager.apply('/dev/video0', self.identity, {'brightness': 0})
        self.assertEqual(self.writes, [])

    def test_changed_device_never_written(self):
        with self.assertRaises(OSError):
            self.manager.apply('/dev/video0', ('other', 1, 2), {'brightness': 10})
        self.assertEqual(self.writes, [])

    def test_failed_partial_update_rolls_back(self):
        original = self.command
        def fail(device, arg):
            if arg == '--set-ctrl=contrast=40':
                raise OSError('test driver failure')
            return original(device, arg)
        self.manager.command = fail
        with self.assertRaises(OSError):
            self.manager.apply('/dev/video0', self.identity, {'brightness': 10, 'contrast': 40})
        self.assertEqual(self.values['brightness'], 0)
        self.assertEqual(self.writes, [('brightness', 10), ('brightness', 0)])

    def test_auto_controls_are_applied_first(self):
        self.manager.apply('/dev/video0', self.identity, {'brightness': 10, 'auto_exposure': 1})
        self.assertEqual(self.writes[0], ('auto_exposure', 1))

    def test_reload_and_idle_no_repeated_io(self):
        self.profile()
        self.targets()
        self.manager.tick()
        self.assertEqual(self.manager.snapshot()['cameras']['front']['state'], 'applied')
        with patch.object(self.manager, 'command', side_effect=AssertionError('idle hardware access')):
            self.manager.tick()
        self.profile('front: {brightness: 20}\n')
        self.manager.tick()
        self.assertEqual(self.values['brightness'], 20)
        self.assertEqual(self.manager.version, 2)

    def test_invalid_reload_keeps_last_good(self):
        self.profile()
        self.targets()
        self.manager.tick()
        self.profile('front: {brightness: bad}\n')
        self.manager.tick()
        self.assertEqual(self.manager.profile['front']['brightness'], 10)
        self.assertEqual(self.manager.snapshot()['config']['state'], 'error')
        self.assertEqual(self.manager.version, 1)

    def test_reconnection_reapplies(self):
        self.profile('front: {brightness: 10}\n')
        self.targets()
        self.manager.tick()
        self.manager.update_devices({})
        self.manager.tick()
        self.values['brightness'] = 0
        self.identity = ('/dev/video0', 789, 456)
        self.targets()
        self.manager.tick()
        self.assertEqual(self.values['brightness'], 10)
        self.assertEqual(self.manager.snapshot()['cameras']['front']['state'], 'applied')

    def test_bad_front_does_not_block_rear(self):
        self.profile('front: {brightness: 128}\nrear: {contrast: 40}\n')
        self.targets()
        self.manager.tick()
        self.assertEqual(self.manager.snapshot()['cameras']['front']['state'], 'error')
        self.assertEqual(self.manager.snapshot()['cameras']['rear']['state'], 'applied')
        self.assertEqual(self.values['contrast'], 40)
        self.profile('front: {brightness: 10}\nrear: {contrast: 40}\n')
        self.manager.tick()
        self.assertEqual(self.manager.snapshot()['cameras']['front']['state'], 'applied')

    def test_slow_driver_does_not_hold_supervisor_lock(self):
        self.profile('front: {brightness: 10}\n')
        self.targets()
        entered, release = threading.Event(), threading.Event()
        original = self.command
        def slow(device, arg):
            entered.set()
            release.wait(3)
            return original(device, arg)
        self.manager.command = slow
        self.manager.start()
        try:
            self.assertTrue(entered.wait(2))
            # These are the supervisor's only interactions with the worker.
            self.manager.update_devices({})
            self.assertEqual(self.manager.snapshot()['config']['state'], 'loaded')
            self.assertFalse(release.is_set())
        finally:
            release.set()
            self.manager.stop()


if __name__ == '__main__':
    unittest.main()

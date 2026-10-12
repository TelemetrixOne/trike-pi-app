#!/usr/bin/python3
"""Validate local controls and frame progress; optional bounded hot-reload test."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--test-reload', action='store_true', help='Briefly perturb front brightness by one unit, then verify automatic restoration without video restart (run with sudo)')
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('video', '/opt/hpr/gateway/runtime/video/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)
config = Path('/etc/hpr/camera-controls.yaml')
profile = video.load_picture_controls(config)
manager = video.CameraControls(config)
status_path = Path('/run/hpr-video/video-status.json')


def status():
    data = json.loads(status_path.read_text())
    assert time.monotonic() - data['at'] < 5, 'Supervisor status is stale'
    return data


def verify(data):
    assert data['camera_controls']['config']['state'] == 'loaded', data['camera_controls']
    for role, values in profile.items():
        if not values:
            continue
        camera = data['camera_controls']['cameras'][role]
        if camera['state'] == 'waiting_for_camera':
            print(f'{role}: disconnected; pending hardware validation')
            continue
        assert camera['state'] == 'applied', camera
        caps = video.parse_picture_capabilities(manager.command(camera['device'], '--list-ctrls-menus'))
        assert {name: caps[name]['value'] for name in values} == values, (role, caps)
        print(f'{role}: managed hardware controls match local file')


before = status()
verify(before)
time.sleep(3)
after = status()
for name, child in before['children'].items():
    current = after['children'][name]
    assert current['pid'] == child['pid'], f'{name} restarted'
    assert current.get('frames', 0) > child.get('frames', 0), f'{name} frames not advancing'
    print(f'{name}: frames advancing, PID stable')
if args.test_reload:
    camera = after['camera_controls']['cameras']['front']
    device = camera['device']
    identity = manager.identity(device)
    desired = profile['front']['brightness']
    caps = video.parse_picture_capabilities(manager.command(device, '--list-ctrls-menus'))
    changed = desired + 1 if desired < caps['brightness']['max'] else desired - 1
    start_version = after['camera_controls']['config']['version']
    try:
        manager.apply(device, identity, {'brightness': changed})
        config.touch()  # Content is unchanged: trigger the actual supervisor reload.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = status()
            state = current['camera_controls']['cameras']['front']
            if (current['camera_controls']['config'].get('version', 0) > start_version
                    and state.get('version') == current['camera_controls']['config']['version']):
                break
            time.sleep(0.25)
        else:
            raise AssertionError('Hot reload did not complete')
        verify(current)
        for name, child in after['children'].items():
            assert current['children'][name]['pid'] == child['pid'], f'{name} restarted during tuning'
        print('Hot reload restored brightness; capture/publish/display PIDs unchanged')
    finally:
        if manager.identity(device) == identity:
            manager.apply(device, identity, {'brightness': desired})
print('PASS: local camera controls and live video')

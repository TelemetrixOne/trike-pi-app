#!/usr/bin/env python3
"""Verify effective local stream quality and advancing native video workers."""
import json
from pathlib import Path
import subprocess
import time

from hpr_gateway.config import load_config
from hpr_gateway.video_local import merge_local, read_local

settings = merge_local(load_config('/etc/hpr/hpr.yaml').get('video', {}), read_local('/etc/hpr/video-local.yaml'))
assert not Path('/run/systemd/system/hpr-video-compositor.service.d/90-stream-trial.conf').exists(), 'Temporary trial drop-in still active'
start = subprocess.check_output(['systemctl', 'show', 'hpr-video-compositor.service', '-p', 'ExecStart', '--value'], text=True)
assert 'runtime/video/hpr-video-compose.sh' in start, 'Normal video startup path is not restored'
deadline, before, reason = time.monotonic() + 30, None, 'no progress status'
while time.monotonic() < deadline:
    try:
        current = json.loads(Path('/run/hpr-video/video-status.json').read_text())
        assert time.monotonic() - current['at'] < 5, 'Stale status'
        publishers = [name for name in current['children'] if name.endswith('-publish')]
        assert publishers, 'No publisher running'
        for name in publishers:
            role = name.removesuffix('-publish')
            camera = settings['cameras'][role]
            width, height = camera.get('stream_size', '640x360').split('x')
            bitrate = str(camera.get('bitrate', '700k'))
            kbps = int(float(bitrate[:-1]) * 1000) if bitrate.endswith('M') else (int(bitrate[:-1]) if bitrate.endswith('k') else int(bitrate) // 1000)
            pid = current['children'][name]['pid']
            command = Path(f'/proc/{pid}/cmdline').read_bytes().decode().replace('\0', ' ')
            assert f'width={width},height={height}' in command, f'{role}: wrong resolution'
            assert f'bitrate={kbps} ' in command, f'{role}: wrong bitrate'
            assert f"framerate={camera.get('output_framerate', 25)}/1" in command, f'{role}: wrong frame rate'
        if before and current['at'] - before['at'] >= 3:
            assert set(current['children']) == set(before['children']), 'Video workers changed'
            for name, old in before['children'].items():
                new = current['children'][name]
                assert old['pid'] == new['pid'], f'{name}: restarted'
                assert new.get('frames', 0) > old.get('frames', 0), f'{name}: stalled'
            for name in publishers:
                role = name.removesuffix('-publish')
                camera = settings['cameras'][role]
                print(f"PASS {role}: {camera.get('stream_size')}, {camera.get('output_framerate',25)} fps, {camera.get('bitrate')}")
            print('PASS normal startup, local quality, stable PIDs and advancing frames')
            break
        if before is None:
            before = current
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        reason, before = str(exc), None
    time.sleep(0.5)
else:
    raise SystemExit(f'FAIL local video validation: {reason}')

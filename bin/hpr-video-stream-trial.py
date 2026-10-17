#!/usr/bin/env python3
"""Temporary front-stream trial; stop returns to current central configuration."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path('/opt/hpr/gateway')
BACKUP_ROOT = Path('/opt/hpr/backups')
STATE = Path('/run/hpr-video-stream-trial/trial.json')
DROPIN = Path('/run/systemd/system/hpr-video-compositor.service.d/90-stream-trial.conf')
UNIT = 'hpr-video-compositor.service'
MARKER = '# HPR temporary front-stream trial; managed by hpr-video-stream-trial.py\n'
DROPIN_TEXT = (MARKER + '[Service]\nExecStart=\nExecStart=/opt/hpr/gateway/venv/bin/python '
               '/opt/hpr/gateway/bin/hpr-video-stream-trial.py run\n')


def trial_settings(settings, overrides):
    if set(overrides) != {'stream_size', 'bitrate'}:
        raise ValueError('Only front stream size and bitrate may be overridden')
    size = str(overrides['stream_size'])
    if size not in ('640x360', '800x450', '960x540', '1280x720'):
        raise ValueError('Use a supported landscape 16:9 test size')
    if not re.fullmatch(r'[1-9][0-9]*k', str(overrides['bitrate'])):
        raise ValueError('Bitrate must be integer kbps, such as 1500k')
    if not 300 <= int(overrides['bitrate'][:-1]) <= 8000:
        raise ValueError('Trial bitrate must be 300..8000 kbps')
    result = copy.deepcopy(settings)
    if not result.get('cameras', {}).get('front', {}).get('enabled', True):
        raise ValueError('Front camera is disabled centrally')
    result.setdefault('cameras', {}).setdefault('front', {}).update(overrides)
    return result


def central_video():
    sys.path.insert(0, str(ROOT))
    from hpr_gateway.config import load_config
    return load_config(os.environ.get('HPR_CONFIG_PATH', '/etc/hpr/hpr.yaml')).get('video', {})


def systemctl(*args):
    return subprocess.check_output(['systemctl', *args], text=True, timeout=40).strip()


def read_status():
    data = json.loads(Path('/run/hpr-video/video-status.json').read_text())
    if time.monotonic() - data['at'] > 5:
        raise RuntimeError('Video progress status is stale')
    return data


def verify(overrides, expected_children):
    deadline, before = time.monotonic() + 30, None
    while time.monotonic() < deadline:
        try:
            current = read_status()
            children = current['children']
            if not expected_children <= set(children):
                raise RuntimeError('Waiting for original video workers')
            front = children['front-publish']
            command = Path(f"/proc/{front['pid']}/cmdline").read_bytes().decode().replace('\0', ' ')
            width, height = overrides['stream_size'].split('x')
            if f'width={width},height={height}' not in command or f"bitrate={overrides['bitrate'][:-1]} " not in command:
                raise RuntimeError('Front publisher settings do not match trial')
            if before and current['at'] - before['at'] >= 3:
                for name in expected_children:
                    old, new = before['children'][name], children[name]
                    if new['pid'] != old['pid'] or new.get('frames', 0) <= old.get('frames', 0):
                        raise RuntimeError(f'{name} is restarting or not advancing')
                return current
            if before is None:
                before = current
        except (OSError, ValueError, KeyError, RuntimeError):
            before = None
        time.sleep(0.5)
    raise RuntimeError('Trial did not establish stable frame progress within 30 seconds')


def stop():
    if not DROPIN.exists():
        if STATE.exists():
            raise RuntimeError('Trial state exists without its drop-in; inspect before changing')
        print('No trial active; central video settings already apply')
        return
    if DROPIN.read_text() != DROPIN_TEXT:
        raise RuntimeError('Unidentified drop-in; refusing to overwrite/remove it')
    # Remove only our exact, runtime-only override. Preserve the trial file in backup.
    if STATE.exists():
        state = json.loads(STATE.read_text())
        backup = Path(state['backup'])
        if backup.parent != BACKUP_ROOT or not backup.name.startswith('video-stream-trial-'):
            raise RuntimeError('Invalid trial backup identity')
        shutil.copy2(STATE, backup / 'trial-before-stop.json')
    DROPIN.unlink()
    systemctl('daemon-reload')
    systemctl('restart', UNIT)
    if systemctl('is-active', UNIT) != 'active':
        raise RuntimeError('Central video service failed to start')
    STATE.unlink(missing_ok=True)
    current = central_video().get('cameras', {}).get('front', {})
    print(f"Trial removed. Front uses central settings: {current.get('stream_size')}, {current.get('bitrate')}")


def start(size, bitrate):
    if STATE.exists() or DROPIN.exists():
        raise RuntimeError('A trial/override already exists; run stop first')
    if systemctl('is-active', UNIT) != 'active':
        raise RuntimeError('Video service is not active')
    settings = central_video()
    overrides = {'stream_size': size, 'bitrate': bitrate}
    trial_settings(settings, overrides)
    original_status = read_status()
    if 'front-publish' not in original_status['children']:
        raise RuntimeError('Front stream is not currently running')
    expected = set(original_status['children'])
    backup = BACKUP_ROOT / ('video-stream-trial-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2('/etc/hpr/hpr.yaml', backup / 'hpr.yaml')
    shutil.copy2('/etc/hpr/camera-controls.yaml', backup / 'camera-controls.yaml')
    (backup / 'unit.txt').write_text(systemctl('cat', UNIT) + '\n')
    (backup / 'video-status.json').write_text(json.dumps(original_status))
    baseline = {k: settings.get('cameras', {}).get('front', {}).get(k)
                for k in ('stream_size', 'bitrate', 'output_framerate', 'gop')}
    state = {'schema': 'hpr.video_stream_trial.v1', 'overrides': overrides,
             'baseline': baseline, 'backup': str(backup)}
    import grp
    group = systemctl('show', UNIT, '-p', 'Group', '--value')
    gid = grp.getgrnam(group).gr_gid
    STATE.parent.mkdir(mode=0o750, exist_ok=True)
    os.chown(STATE.parent, 0, gid)
    try:
        descriptor = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(state, handle)
        os.chown(STATE, 0, gid)
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        with DROPIN.open('x') as handle:
            handle.write(DROPIN_TEXT)
        systemctl('daemon-reload')
        systemctl('restart', UNIT)
        verify(overrides, expected)
    except Exception:
        if DROPIN.exists() and DROPIN.read_text() == DROPIN_TEXT:
            stop()
        else:
            STATE.unlink(missing_ok=True)
        raise
    print(f'Front trial active: {size}, {bitrate}; rear/capture/HDMI settings unchanged')
    print(f'Backup: {backup}')
    print('Rollback: sudo /opt/hpr/gateway/venv/bin/python /opt/hpr/gateway/bin/hpr-video-stream-trial.py stop')


def run():
    settings = central_video()  # Always reload authoritative config; never clone credentials.
    try:
        state = json.loads(STATE.read_text())
        if state['schema'] != 'hpr.video_stream_trial.v1':
            raise ValueError('Unknown trial schema')
        settings = trial_settings(settings, state['overrides'])
    except (OSError, ValueError, KeyError) as exc:
        print(f'Trial unavailable ({type(exc).__name__}); using central video settings', flush=True)
    environment = dict(os.environ, HPR_VIDEO_SETTINGS=json.dumps(settings))
    os.execve('/usr/bin/python3', ['/usr/bin/python3', str(ROOT / 'runtime/video/hpr-video-native.py')], environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'run'))
    parser.add_argument('--size', default='800x450')
    parser.add_argument('--bitrate', default='1500k')
    args = parser.parse_args()
    if args.action == 'run':
        run()
        return
    if os.geteuid() != 0:
        parser.error('start/stop require sudo')
    if args.action == 'start':
        start(args.size, args.bitrate)
    else:
        stop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Video trial failed: {exc}', file=sys.stderr)
        sys.exit(1)

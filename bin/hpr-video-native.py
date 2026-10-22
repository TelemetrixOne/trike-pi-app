#!/usr/bin/python3
"""Isolated USB captures, RTSP publishers and front-only KMS viewer.

Supervise monotonic frame progress, not just process liveness. GStreamer runs
in disposable children so a stuck display cannot take down healthy streams.
"""
import glob
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

QUEUE = 'queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream'
STARTUP_GRACE = 15
STALL_TIMEOUT = 2

# Local USB-camera tuning is intentionally separate from central race settings.
PICTURE_CONTROLS = {
    'brightness', 'contrast', 'gamma', 'saturation', 'sharpness', 'hue', 'gain',
    'backlight_compensation', 'power_line_frequency', 'auto_exposure',
    'exposure_time_absolute', 'white_balance_automatic', 'white_balance_temperature',
}


def load_picture_controls(path):
    import yaml
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError(f'Duplicate camera/control key: {key}')
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    raw = Path(path).read_bytes()
    if len(raw) > 65536:
        raise ValueError('Camera controls file exceeds 64 KiB')
    data = yaml.load(raw, Loader=UniqueLoader)
    if not isinstance(data, dict) or set(data) - {'front', 'rear'}:
        raise ValueError('Camera controls must contain only front/rear mappings')
    for role, controls in data.items():
        if not isinstance(controls, dict):
            raise ValueError(f'{role} must contain control names and integer values')
        for name, value in controls.items():
            if name not in PICTURE_CONTROLS or type(value) is not int:
                raise ValueError(f'Unsupported control or non-integer value: {role}.{name}')
        if 'exposure_time_absolute' in controls and controls.get('auto_exposure') != 1:
            raise ValueError(f'{role}: manual exposure time requires auto_exposure: 1')
        if 'white_balance_temperature' in controls and controls.get('white_balance_automatic') != 0:
            raise ValueError(f'{role}: colour temperature requires white_balance_automatic: 0')
    return data


def parse_picture_capabilities(output):
    controls, current = {}, None
    for line in output.splitlines():
        match = re.match(r'\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)', line)
        if match:
            name, kind, fields = match.groups()
            values = {key: int(value) for key, value in
                      re.findall(r'(min|max|step|value)=(-?\d+)', fields)}
            if kind == 'bool':
                values.update(min=0, max=1, step=1)
            values['choices'] = [] if kind == 'menu' else None
            controls[name] = values
            current = name
        elif current and controls[current]['choices'] is not None:
            match = re.match(r'\s*(\d+):', line)
            if match:
                controls[current]['choices'].append(int(match[1]))
    return controls


class CameraControls:
    """Reload local tuning and apply it without blocking the video supervisor."""

    def __init__(self, path='/etc/hpr/camera-controls.yaml'):
        self.path = Path(path)
        self.targets = {}
        self.profile = {}
        self.marker = None
        self.version = 0
        self.applied = {}
        self.retry = {}
        self.status = {'config': {'state': 'starting', 'path': str(self.path)}, 'cameras': {}}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name='camera-controls', daemon=True)

    def start(self):
        self.thread.start()

    def update_devices(self, targets):
        with self.lock:
            self.targets = dict(targets)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.status))

    def reload(self):
        try:
            stat = self.path.stat()
            marker = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            marker = ('unreadable', str(exc))
        if marker == self.marker:
            return
        self.marker = marker
        try:
            profile = load_picture_controls(self.path)
        except Exception as exc:
            state = {'state': 'error', 'path': str(self.path), 'error': str(exc),
                     'using_last_good': bool(self.profile)}
            print(f'Camera controls config rejected: {exc}', file=sys.stderr, flush=True)
        else:
            self.profile = profile
            self.version += 1
            self.retry.clear()
            state = {'state': 'loaded', 'path': str(self.path), 'version': self.version}
        with self.lock:
            self.status['config'] = state

    def command(self, device, *args):
        result = subprocess.run(['v4l2-ctl', '--device', device, *args],
                                text=True, capture_output=True, timeout=2, check=True)
        return result.stdout

    @staticmethod
    def identity(device):
        stat = os.stat(device)
        return os.path.realpath(device), stat.st_ino, getattr(stat, 'st_rdev', 0)

    def apply(self, device, identity, desired):
        def check_device():
            if self.stopping.is_set() or self.identity(device) != identity:
                raise OSError('Camera disconnected or changed during control update')
        check_device()
        available = parse_picture_capabilities(self.command(device, '--list-ctrls-menus'))
        # Validate the complete camera profile before writing any setting.
        for name, value in desired.items():
            control = available.get(name)
            if control is None or 'min' not in control or 'max' not in control:
                raise ValueError(f'{name} is not supported by this camera')
            if not control['min'] <= value <= control['max'] or (
                    value - control['min']) % max(1, control.get('step', 1)):
                raise ValueError(f'{name}={value} is outside the camera range/step')
            if control['choices'] is not None and value not in control['choices']:
                raise ValueError(f'{name}={value} is not a supported menu choice')
        # Disable/enable automatic modes before dependent manual controls.
        names = sorted(desired, key=lambda name: (name not in
                       ('auto_exposure', 'white_balance_automatic'), name))
        previous = {name: available[name].get('value') for name in names}
        changed = []
        try:
            for name in names:
                if available[name].get('value') != desired[name]:
                    check_device()
                    self.command(device, f'--set-ctrl={name}={desired[name]}')
                    changed.append(name)
            check_device()
            readback = parse_picture_capabilities(self.command(device, '--list-ctrls-menus'))
            actual = {name: readback.get(name, {}).get('value') for name in names}
            if actual != desired:
                raise ValueError(f'Control readback does not match requested values: {actual}')
        except Exception:
            # Restore only controls actually changed, on the same device generation.
            for name in reversed(changed):
                try:
                    check_device()
                    if previous[name] is not None:
                        self.command(device, f'--set-ctrl={name}={previous[name]}')
                except Exception:
                    pass
            raise
        return actual

    def tick(self):
        self.reload()
        with self.lock:
            targets = dict(self.targets)
        for role in ('front', 'rear'):
            target = targets.get(role)
            desired = self.profile.get(role, {})
            if not target:
                self.applied.pop(role, None)
                self.retry.pop(role, None)
                state = {'state': 'waiting_for_camera'}
            elif not desired:
                self.applied.pop(role, None)
                state = {'state': 'unmanaged'}
            else:
                device, identity = target
                token = (identity, self.version)
                if self.applied.get(role) == token or time.monotonic() < self.retry.get(role, 0):
                    continue
                try:
                    actual = self.apply(device, identity, desired)
                except Exception as exc:
                    self.retry[role] = time.monotonic() + 5
                    state = {'state': 'error', 'device': device, 'error': str(exc)}
                    # One log per changed error, not a continuous log flood.
                    if self.snapshot()['cameras'].get(role) != state:
                        print(f'{role} camera controls: {exc}', file=sys.stderr, flush=True)
                else:
                    self.applied[role] = token
                    self.retry.pop(role, None)
                    state = {'state': 'applied', 'device': device, 'controls': actual,
                             'version': self.version}
                    print(f'{role} camera controls applied: {actual}', flush=True)
            with self.lock:
                self.status['cameras'][role] = state

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f'Camera controls worker: {exc}', file=sys.stderr, flush=True)
            self.stopping.wait(0.5)

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=2)


def quote(value):
    return json.dumps(str(value))


def dimensions(value):
    if not re.fullmatch(r'[1-9][0-9]*x[1-9][0-9]*', str(value)):
        raise ValueError(f'Invalid video dimensions: {value}')
    return tuple(map(int, value.split('x')))


def rotation(camera):
    return {'none': 'identity', '180': '180', 'clockwise_90': '90r',
            'anticlockwise_90': '90l'}[str(camera.get('rotation', 'none'))]


def jpeg_caps(size, fps):
    width, height = dimensions(size)
    return f'image/jpeg,width={width},height={height},framerate={int(fps)}/1,pixel-aspect-ratio=1/1'


def capture_pipeline(device, socket, size, fps, display_socket=None):
    pipeline = (f'v4l2src device={quote(device)} io-mode=mmap do-timestamp=true ! '
                f'{jpeg_caps(size, fps)} ! identity name=progress silent=true ! tee name=frames ')
    # Separate SHM rings: a stopped HDMI reader must not pin the publisher's
    # buffers. Each branch drops independently when its ring fills.
    for target in [socket] + ([display_socket] if display_socket else []):
        pipeline += (f'frames. ! {QUEUE} ! shmsink socket-path={quote(target)} '
                     'shm-size=8388608 wait-for-connection=false sync=false '
                     'async=false enable-last-sample=false ')
    return pipeline


def source_pipeline(socket, size, fps):
    return (f'shmsrc socket-path={quote(socket)} is-live=true do-timestamp=true ! '
            f'{jpeg_caps(size, fps)} ! {QUEUE} ! jpegdec')


def display_pipeline(socket, size, fps, camera, mode):
    connector, width, height = mode
    return (f'{source_pipeline(socket, size, fps)} ! videoflip video-direction={rotation(camera)} '
            '! videoscale add-borders=true ! videoconvert ! '
            f'video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1,format=BGRA ! '
            'kmssink name=progress driver-name=vc4 '
            f'connector-id={connector} sync=false async=false processing-deadline=0 skip-vsync=true '
            'force-modesetting=false enable-last-sample=false')


def publisher_pipeline(socket, size, fps, camera, role, port):
    width, height = dimensions(camera.get('stream_size', '640x360'))
    bitrate = str(camera.get('bitrate', '700k'))
    kbps = int(float(bitrate[:-1]) * 1000) if bitrate.endswith('M') else (
        int(bitrate[:-1]) if bitrate.endswith('k') else int(bitrate) // 1000)
    return (f'{source_pipeline(socket, size, fps)} ! videoflip video-direction={rotation(camera)} '
            '! videoconvert ! videoscale add-borders=true ! videorate drop-only=true ! '
            f'video/x-raw,width={width},height={height},framerate={int(camera.get("output_framerate", 25))}/1,format=I420,pixel-aspect-ratio=1/1 ! '
            f'{os.environ.get("HPR_VIDEO_GST_TIMESTAMP", "")}'
            'x264enc speed-preset=ultrafast tune=zerolatency '
            f'bitrate={kbps} key-int-max={int(camera.get("gop", 25))} bframes=0 ! '
            'h264parse config-interval=1 ! identity name=progress silent=true ! '
            f'rtspclientsink location={quote("rtsp://127.0.0.1:" + str(port) + "/" + camera.get("path", role))} protocols=tcp')


def resolve_devices(cameras, candidates=None):
    """Preserve identifiable roles before assigning unclaimed USB cameras."""
    candidates = sorted(candidates if candidates is not None else
                        glob.glob('/dev/v4l/by-path/*usbv2*video-index0'))
    result, used = {}, set()
    for role, camera in cameras.items():
        path = camera.get('device', '')
        if camera.get('enabled', True) and path and os.path.exists(path):
            real = os.path.realpath(path)
            if real not in used:
                result[role] = path
                used.add(real)
    for role, camera in cameras.items():
        if role in result or not camera.get('enabled', True):
            continue
        for path in candidates:
            real = os.path.realpath(path)
            if real not in used and os.path.exists(path):
                result[role] = path
                used.add(real)
                break
    return result


def display_mode():
    try:
        output = subprocess.check_output(['kmsprint'], text=True, timeout=2, stderr=subprocess.DEVNULL)
        connector = None
        for line in output.splitlines():
            if line.startswith('Connector '):
                match = re.match(r'Connector \d+ \((\d+)\) HDMI-A-\d+ \(connected\)', line)
                connector = int(match[1]) if match else None
            elif connector is not None:
                match = re.search(r'Crtc \d+ \(\d+\) (\d+)x(\d+)@', line)
                if match:
                    return connector, int(match[1]), int(match[2])
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def write_json(path, value):
    temporary = str(path) + '.new'
    Path(temporary).write_text(json.dumps(value))
    os.replace(temporary, path)


def read_progress(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def stalled(started, progress, now):
    if 'at' in progress:
        return now - progress['at'] > STALL_TIMEOUT
    return now - started > STARTUP_GRACE


def clock_synchronised():
    try:
        return subprocess.check_output(
            ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
            text=True, timeout=1, stderr=subprocess.DEVNULL).strip() == 'yes'
    except (OSError, subprocess.SubprocessError):
        return False


def worker(description, heartbeat, kind):
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    Gst.init(None)
    if kind == 'publish':
        os.nice(5)
    pipeline = Gst.parse_launch(description)
    element = pipeline.get_by_name('progress')
    stamp = pipeline.get_by_name('burned_timestamp')
    stamp_visible = [False]
    def update_clock():
        stamp_visible[0] = clock_synchronised()
        stamp.set_property('silent', not stamp_visible[0])
        return True
    if stamp is not None:
        # Never block camera delivery waiting for NTP. Hide untrusted wall time
        # until synchronised, then reveal it in place without restarting video.
        update_clock()
        GLib.timeout_add_seconds(15, update_clock)
    frames, previous = [0], [0]
    # Completed sink renders detect a blocked KMS sink; a probe before it
    # would incorrectly report queued frames as displayed.
    if kind != 'display':
        def buffer_probe(pad, info):
            frames[0] += 1
            return Gst.PadProbeReturn.OK
        element.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, buffer_probe)
    loop, failed = GLib.MainLoop(), [False]

    def progress():
        count = frames[0] if kind != 'display' else element.get_property('stats').get_value('rendered')
        if count != previous[0]:
            write_json(heartbeat, {'at': time.monotonic(), 'frames': count,
                                   'timestamp_visible': stamp_visible[0]})
            previous[0] = count
        return True

    def message(bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            error, detail = msg.parse_error()
            print(f'{kind}: {error}: {detail}', file=sys.stderr, flush=True)
            failed[0] = True
            loop.quit()
        elif msg.type == Gst.MessageType.EOS:
            failed[0] = True
            loop.quit()

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect('message', message)
    GLib.timeout_add(500, progress)
    signal.signal(signal.SIGTERM, lambda *_: loop.quit())
    signal.signal(signal.SIGINT, lambda *_: loop.quit())
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
    return int(failed[0])


class Child:
    def __init__(self, name, kind, description, directory):
        self.name, self.kind, self.description = name, kind, description
        self.heartbeat = directory / (name + '.json')
        self.heartbeat.unlink(missing_ok=True)
        self.started = time.monotonic()
        self.process = subprocess.Popen([sys.executable, __file__, '--worker', description,
                                         str(self.heartbeat), kind], stdin=subprocess.DEVNULL)
        print(f'Started {name} pid={self.process.pid}', flush=True)

    def unhealthy(self):
        return self.process.poll() is not None or stalled(
            self.started, read_progress(self.heartbeat), time.monotonic())

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.heartbeat.unlink(missing_ok=True)


def supervise(settings):
    cameras = {role: settings.get('cameras', {}).get(role, {}) for role in ('front', 'rear')}
    display = settings.get('display', {})
    size = display.get('capture_size', '1280x720')
    fps = int(display.get('capture_framerate', 30))
    root = Path(os.environ.get('RUNTIME_DIRECTORY', '/tmp'))
    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))
    children, identities, restarts, retry_at = {}, {}, {}, {}
    controls = CameraControls()
    controls.start()
    with tempfile.TemporaryDirectory(prefix='hpr-video-', dir=root) as directory:
        directory = Path(directory)

        def stop(name):
            child = children.pop(name, None)
            if child:
                child.stop()

        def ensure(name, kind, description):
            child = children.get(name)
            if child and (child.description != description or child.unhealthy()):
                print(f'Recovering {name}: changed pipeline, exit or stalled frames', flush=True)
                stop(name)
                restarts[name] = restarts.get(name, 0) + 1
                retry_at[name] = time.monotonic() + 0.5
            if name not in children and time.monotonic() >= retry_at.get(name, 0):
                children[name] = Child(name, kind, description, directory)

        try:
            while running[0]:
                devices = resolve_devices(cameras)
                for role, camera in cameras.items():
                    camera_size = camera.get('capture_size', size)
                    device = devices.get(role)
                    try:
                        stat = os.stat(device) if device else None
                        identity = (os.path.realpath(device), stat.st_ino, getattr(stat, 'st_rdev', 0)) if stat else None
                    except OSError:
                        identity = None
                    capture = children.get(role + '-capture')
                    if identity != identities.get(role) or (capture and capture.unhealthy()):
                        # Only this camera's consumers reconnect to its new socket.
                        if role == 'front':
                            stop('hdmi')
                        stop(role + '-publish')
                        stop(role + '-capture')
                        (directory / (role + '.sock')).unlink(missing_ok=True)
                        if role == 'front':
                            (directory / 'display.sock').unlink(missing_ok=True)
                        identities[role] = identity
                        restarts[role + '-capture'] = restarts.get(role + '-capture', 0) + 1
                        print(f'{role} camera state changed; reconnecting its consumers', flush=True)
                    if identity:
                        socket = directory / (role + '.sock')
                        ensure(role + '-capture', 'capture', capture_pipeline(
                            device, socket, camera_size, fps, directory / 'display.sock' if role == 'front' else None))
                        if socket.exists():
                            ensure(role + '-publish', 'publish', publisher_pipeline(
                                socket, camera_size, fps, camera, role, settings.get('rtsp_port', 8554)))
                controls.update_devices({role: (device, identities[role]) for role, device in devices.items()
                                         if identities.get(role)})
                mode = display_mode() if display.get('enabled', True) else None
                socket = directory / 'display.sock'
                if mode and identities.get('front') and socket.exists():
                    ensure('hdmi', 'display', display_pipeline(
                        socket, cameras['front'].get('capture_size', size), fps, cameras['front'], mode))
                else:
                    stop('hdmi')
                write_json(root / 'video-status.json', {
                    'at': time.monotonic(), 'display_mode': mode, 'pip': False,
                    'timestamp_requested': os.environ.get('HPR_VIDEO_TIMESTAMP_ENABLED') == '1',
                    'timestamp_timezone': os.environ.get('TZ'),
                    'camera_controls': controls.snapshot(),
                    'children': {name: {'pid': child.process.pid, 'restarts': restarts.get(name, 0),
                                       **read_progress(child.heartbeat)} for name, child in children.items()}})
                time.sleep(0.5)
        finally:
            controls.stop()
            for name in list(children):
                stop(name)
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        sys.exit(worker(*sys.argv[2:]))
    sys.exit(supervise(json.loads(os.environ['HPR_VIDEO_SETTINGS'])))

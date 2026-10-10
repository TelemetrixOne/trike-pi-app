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
import time

QUEUE = 'queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream'
STARTUP_GRACE = 15
STALL_TIMEOUT = 2
DEMO_SPLASH_MARKER = Path('/run/hpr-demo-show/hdmi-splash.json')


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


def display_drain_pipeline(socket, size, fps):
    # Keep the HDMI SHM ring connected while the splash owns KMS. Without a
    # reader, GStreamer's shmsink can release its ring and the returning camera
    # viewer cannot reconnect without restarting capture (and the front stream).
    return (f'shmsrc socket-path={quote(socket)} is-live=true do-timestamp=true ! '
            f'{jpeg_caps(size, fps)} ! {QUEUE} ! identity name=progress silent=true ! '
            'fakesink sync=false async=false enable-last-sample=false')


def display_pipeline(socket, size, fps, camera, mode):
    connector, width, height = mode
    return (f'{source_pipeline(socket, size, fps)} ! videoflip video-direction={rotation(camera)} '
            '! videoscale add-borders=true ! videoconvert ! '
            f'video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1,format=BGRA ! '
            'kmssink name=progress driver-name=vc4 '
            f'connector-id={connector} sync=false async=false processing-deadline=0 skip-vsync=true '
            'force-modesetting=false enable-last-sample=false')


def splash_pipeline(image, mode):
    connector, width, height = mode
    return (f'filesrc location={quote(image)} ! pngdec ! imagefreeze ! '
            'video/x-raw,framerate=10/1 ! videoscale add-borders=true ! videoconvert ! '
            f'video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1,format=BGRA ! '
            'kmssink name=progress driver-name=vc4 '
            f'connector-id={connector} sync=false async=false processing-deadline=0 skip-vsync=true '
            'force-modesetting=false enable-last-sample=false')


def demo_splash_requested(marker=DEMO_SPLASH_MARKER, now=None):
    try:
        request = json.loads(marker.read_text())
        return float(request['expires_at']) > (time.time() if now is None else now)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def choose_hdmi_source(mode, image_available, front_ready, first_display_at, now, demo_active):
    if not mode:
        return None
    if image_available and (demo_active or (first_display_at is not None and
                            (now - first_display_at < 3 or not front_ready))):
        return 'splash'
    return 'camera' if front_ready else None


def publisher_pipeline(socket, size, fps, camera, role, port):
    width, height = dimensions(camera.get('stream_size', '640x360'))
    bitrate = str(camera.get('bitrate', '700k'))
    kbps = int(float(bitrate[:-1]) * 1000) if bitrate.endswith('M') else (
        int(bitrate[:-1]) if bitrate.endswith('k') else int(bitrate) // 1000)
    return (f'{source_pipeline(socket, size, fps)} ! videoflip video-direction={rotation(camera)} '
            '! videoconvert ! videoscale ! videorate drop-only=true ! '
            f'video/x-raw,width={width},height={height},framerate={int(camera.get("output_framerate", 25))}/1,format=I420 ! '
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


def worker(description, heartbeat, kind):
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst, GLib
    Gst.init(None)
    if kind in ('publish', 'drain'):
        os.nice(5)
    pipeline = Gst.parse_launch(description)
    element = pipeline.get_by_name('progress')
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
            write_json(heartbeat, {'at': time.monotonic(), 'frames': count})
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
    image = Path(os.environ.get('HPR_INSTALL_ROOT', '/opt/hpr/gateway')) / 'runtime/video/startup-splash.png'
    first_display_at = None
    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))
    children, identities, restarts, retry_at = {}, {}, {}, {}
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
                            device, socket, size, fps, directory / 'display.sock' if role == 'front' else None))
                        if socket.exists():
                            ensure(role + '-publish', 'publish', publisher_pipeline(
                                socket, size, fps, camera, role, settings.get('rtsp_port', 8554)))
                mode = display_mode() if display.get('enabled', True) else None
                socket = directory / 'display.sock'
                if mode and first_display_at is None:
                    first_display_at = time.monotonic()
                front_capture = children.get('front-capture')
                front_ready = bool(identities.get('front') and socket.exists() and front_capture
                                   and read_progress(front_capture.heartbeat).get('frames', 0) > 0)
                splash_wanted = choose_hdmi_source(
                    mode, image.is_file(), front_ready, first_display_at,
                    time.monotonic(), demo_splash_requested())
                hdmi_source = splash_wanted
                camera_description = (display_pipeline(socket, size, fps, cameras['front'], mode)
                                      if mode and front_ready else None)
                if hdmi_source == 'splash' and front_ready:
                    ensure('hdmi-drain', 'drain', display_drain_pipeline(socket, size, fps))
                    drain = children.get('hdmi-drain')
                    drain_ready = bool(drain and read_progress(drain.heartbeat).get('frames', 0) > 0)
                    current_hdmi = children.get('hdmi')
                    if current_hdmi and current_hdmi.description == camera_description and not drain_ready:
                        hdmi_source = 'camera'  # Do not release the only SHM reader yet.
                if hdmi_source == 'splash':
                    ensure('hdmi', 'display', splash_pipeline(image, mode))
                elif hdmi_source == 'camera':
                    ensure('hdmi', 'display', camera_description)
                else:
                    stop('hdmi')
                hdmi = children.get('hdmi')
                if (splash_wanted != 'splash' and hdmi and hdmi.description == camera_description
                        and read_progress(hdmi.heartbeat).get('frames', 0) > 0):
                    stop('hdmi-drain')
                elif not front_ready:
                    stop('hdmi-drain')
                write_json(root / 'video-status.json', {
                    'at': time.monotonic(), 'display_mode': mode, 'hdmi_source': hdmi_source, 'pip': False,
                    'children': {name: {'pid': child.process.pid, 'restarts': restarts.get(name, 0),
                                       **read_progress(child.heartbeat)} for name, child in children.items()}})
                time.sleep(0.5)
        finally:
            for name in list(children):
                stop(name)
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        sys.exit(worker(*sys.argv[2:]))
    sys.exit(supervise(json.loads(os.environ['HPR_VIDEO_SETTINGS'])))

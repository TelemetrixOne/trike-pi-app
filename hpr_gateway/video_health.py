"""Read native supervisor frame evidence without touching capture or display."""
import json
import math
from pathlib import Path
import time
from datetime import datetime, timezone


class VideoEvidence:
    def __init__(self):
        self.previous = {}

    def inspect(self, config, process, now=None):
        now = time.monotonic() if now is None else now
        maximum_age = 10
        workers, error, document = {}, None, {}
        try:
            path = Path(config.get('video.runtime_status_path', '/run/hpr-video/video-status.json'))
            if path.stat().st_size > 131072:
                raise ValueError('oversized supervisor status')
            document = json.loads(path.read_text())
            required = [name + '-' + kind for name, camera in config.get('video.cameras', {}).items()
                        if camera.get('enabled') for kind in ('capture', 'publish')]
            # A disconnected monitor must never make independent streaming fail.
            if document.get('display_mode') is not None:
                required.append('display')
            for name in required:
                sample = document.get('children', {}).get(name, {})
                frames, stamp, pid = sample.get('frames'), sample.get('at'), sample.get('pid')
                valid = (type(frames) is int and frames > 0 and type(stamp) in (int, float)
                         and math.isfinite(stamp) and 0 <= now - stamp <= maximum_age)
                old = self.previous.get(name)
                if old is None or old[:2] != (pid, frames):
                    self.previous[name] = (pid, frames, stamp if valid else now)
                movement_age = now - self.previous[name][2]
                fresh = valid and 0 <= movement_age <= maximum_age
                workers[name] = {'state': 'fresh' if fresh else 'stale', 'frames': frames,
                                 'frame_age_seconds': round(now - stamp, 1) if valid else None}
            good = bool(required) and all(v['state'] == 'fresh' for v in workers.values())
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            good, error = False, type(exc).__name__
        expected = config.enabled('video')
        overall = ('Disabled' if not expected else process if process != 'Good'
                   else 'Good' if good else 'Failed')
        return {'schema': 'hpr.service_health.v2', 'trike_id': config.trike_id,
                'service': 'video', 'process': 'running' if process == 'Good' else process.lower(),
                'device': 'online' if good else 'missing_or_stalled', 'data': 'fresh' if good else 'stale',
                'overall': overall, 'reason': 'frame_progress' if good else 'frame_progress_missing_or_stale',
                'expected': expected, 'workers': workers, 'error': error,
                'display_connected': document.get('display_mode') is not None,
                'scope': 'capture_publisher_progress_not_measured_display_latency',
                'observed_at': datetime.now(timezone.utc).isoformat()}

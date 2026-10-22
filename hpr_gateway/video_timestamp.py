"""One wall-clock policy for native and legacy network encoders.

Disabled until the Pi performance/latency acceptance test is complete. This
module never starts a clock setter, relay, extra encoder, or background writer.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
from zoneinfo import ZoneInfo

FORMAT = '%H:%M:%S %d-%m-%Y'
GST_FILTER = (
    'clockoverlay name=burned_timestamp silent=true time-format="' + FORMAT + '" '
    'halignment=left valignment=bottom xpad=8 ypad=8 '
    'font-desc="DejaVu Sans 12" auto-resize=false '
    'color=4294967295 shaded-background=true draw-outline=true draw-shadow=false ! '
)
# Explicit 24-hour format; literal colons survive filtergraph, option and text
# expansion parsing. %T is not portable (some strftime implementations go blank).
FF_FILTER = (
    r"drawtext=font='DejaVu Sans':text='%{localtime\:%H\\\:%M\\\:%S %d-%m-%Y}':"
    'fontsize=16:fontcolor=white:x=8:y=h-th-8:box=1:boxcolor=black@0.55:boxborderw=4'
)


def environment(config, environ=None):
    environ = os.environ if environ is None else environ
    enabled = config.get('video.timestamp.enabled', False)
    if type(enabled) is not bool:
        raise ValueError('video.timestamp.enabled must be boolean')
    trial = environ.get('HPR_VIDEO_TIMESTAMP_ENABLED')
    if trial is not None:
        if trial not in ('0', '1'):
            raise ValueError('HPR_VIDEO_TIMESTAMP_ENABLED must be 0 or 1')
        enabled = trial == '1'
    zone = (config.get('video.timestamp.timezone') or config.get('race.timezone')
            or config.get('system.timezone') or environ.get('TZ'))
    if not zone:
        try:
            zone = str(Path('/etc/localtime').resolve()).split('/zoneinfo/', 1)[1]
        except (OSError, IndexError):
            if enabled:
                raise ValueError('Cannot determine race timezone; configure an IANA timezone')
    result = {
        'HPR_VIDEO_TIMESTAMP_ENABLED': '1' if enabled else '0',
        'HPR_VIDEO_GST_TIMESTAMP': GST_FILTER if enabled else '',
        'HPR_VIDEO_FF_TIMESTAMP': ',' + FF_FILTER if enabled else '',
        'LC_TIME': 'C',
    }
    if zone:
        ZoneInfo(zone)  # Reject typos, paths and shell fragments; DST uses tzdata.
        result['TZ'] = zone
    return result


def main():
    from .config import load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hpr/hpr.yaml')
    args = parser.parse_args()
    for key, value in environment(load_config(args.config)).items():
        print(f'export {key}={shlex.quote(value)}')


if __name__ == '__main__':
    main()

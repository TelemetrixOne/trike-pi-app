"""Narrow, persistent Pi-local stream-quality overrides; identities stay central."""
from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
import re

import yaml

from .config import load_config

LOG = logging.getLogger(__name__)
LOCAL_PATH = '/etc/hpr/video-local.yaml'
FIELDS = {'stream_size', 'capture_size', 'output_framerate', 'bitrate'}
SIZES = {'640x360', '800x450', '800x600', '960x540', '1280x720'}


def read_local(path: str) -> dict:
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError(f'Duplicate local-video key: {key}')
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    raw = Path(path).read_bytes()
    if len(raw) > 8192:
        raise ValueError('Local video file exceeds 8 KiB')
    data = yaml.load(raw, Loader=UniqueLoader)
    return {} if data is None else data


def merge_local(settings: dict, overrides: dict) -> dict:
    if not isinstance(overrides, dict) or set(overrides) - {'front', 'rear'}:
        raise ValueError('Local video file accepts front/rear sections only')
    result = copy.deepcopy(settings)
    capture_fps = int(settings.get('display', {}).get('capture_framerate', 30))
    for role, values in overrides.items():
        if role not in result.get('cameras', {}):
            raise ValueError(f'{role} is not configured centrally')
        if not isinstance(values, dict) or set(values) - FIELDS:
            raise ValueError(f'{role}: only capture_size, stream_size, output_framerate and bitrate are local')
        for key, value in values.items():
            if key in {'stream_size', 'capture_size'} and value not in SIZES:
                raise ValueError(f'{role}: unsupported landscape stream size')
            if key == 'output_framerate' and (type(value) is not int or not 1 <= value <= capture_fps):
                raise ValueError(f'{role}: frame rate must be 1..{capture_fps}')
            if key == 'bitrate':
                match = re.fullmatch(r'([1-9][0-9]*(?:\.[0-9]+)?)(k|M)', str(value))
                if not match or not 100 <= float(match[1]) * (1000 if match[2] == 'M' else 1) <= 8000:
                    raise ValueError(f'{role}: bitrate must be 100k..8M')
        result['cameras'][role].update(values)
    return result


def video_settings(config_path: str, local_path: str = LOCAL_PATH) -> dict:
    settings = load_config(config_path).get('video', {})
    try:
        overrides = read_local(local_path)
        merged = merge_local(settings, overrides)
    except FileNotFoundError:
        return settings
    except Exception as exc:
        # An invalid optional file must not prevent either camera from streaming.
        LOG.error('Local video settings rejected (%s); using central video settings', exc)
        return settings
    if overrides:
        LOG.info('Applying Pi-local stream quality from %s: %s', local_path, overrides)
    return merged


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hpr/hpr.yaml')
    parser.add_argument('--local-config', default=LOCAL_PATH)
    args = parser.parse_args()
    print(json.dumps(video_settings(args.config, args.local_config)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

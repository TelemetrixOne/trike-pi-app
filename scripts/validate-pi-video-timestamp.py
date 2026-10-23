#!/usr/bin/env python3
"""Isolated synthetic integration test of the native NTP visibility gate.

Uses the installed native worker and GStreamer, not either physical camera.
Does not change the system clock, NTP, services or production stream settings.
"""
import importlib.util
from pathlib import Path
import tempfile

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib

from hpr_gateway.video_timestamp import GST_FILTER

spec = importlib.util.spec_from_file_location('native_clock_test',
                                             '/opt/hpr/gateway/runtime/video/hpr-video-native.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)
observed = []
answers = iter([False, True, False, True, True, True])
video.clock_synchronised = lambda: next(answers, True)
video.write_json = lambda path, value: observed.append(value)
original = GLib.timeout_add_seconds
GLib.timeout_add_seconds = lambda seconds, callback: original(1, callback)
with tempfile.TemporaryDirectory(prefix='hpr-timestamp-clock-test-') as directory:
    pipeline = ('videotestsrc is-live=true num-buffers=150 ! '
                'video/x-raw,width=320,height=240,framerate=30/1 ! '
                + GST_FILTER + 'identity name=progress silent=true ! fakesink sync=false')
    video.worker(pipeline, str(Path(directory) / 'progress.json'), 'publish')
states = [row['timestamp_visible'] for row in observed]
assert False in states and True in states, f'Clock visibility did not change: {states}'
assert any(a and not b for a, b in zip(states, states[1:])), f'Clock loss not hidden: {states}'
assert all(b['frames'] > a['frames'] for a, b in zip(observed, observed[1:])), 'Synthetic video stalled'
print('PASS isolated native clock gate: unsynchronised hidden, synchronised visible, loss hidden; frames advancing')

# Burned-in stream clock — staged, not yet live-accepted

Both front and rear NETWORK publishers can burn `HH:mm:ss dd-MM-yyyy` (24-hour)
into their transmitted frames, bottom-left with an 8px inset, white text and a
dark contrasting background. The filter runs after rotation/scaling/rate control
and before the EXISTING encode. Native GStreamer uses `clockoverlay`; all legacy
FFmpeg normal/HDMI/PiP network branches use `drawtext`. HDMI/display branches are
unchanged. No relay, extra queue, writer process or second encode is introduced.
Existing resolutions, bitrates, USB controls and central configuration stay intact.

The stamp is encoder wall-clock time, not the exact sensor exposure time. It
survives live players, recordings and downloads because it is part of the pixels.
It is second precision; do not treat it as frame-accurate race timing.

## Timezone and clock

`video_timestamp.environment()` uses existing central `video.timestamp.timezone`,
then `race.timezone`, then `system.timezone`, then the inherited `TZ`, then the
Pi's `/etc/localtime` IANA zone. No race identity, endpoint or Sydney offset is
hard-coded. IANA tzdata handles DST: Sydney UTC+11 in October, UTC+10 in July.
If central configuration does not supply a zone, verify that the Pi's system
zone matches the race (currently Australia/Sydney) BEFORE enabling. This release
does not invent a new web configurator field or alter central synchronisation.
`video.timestamp.enabled` defaults OFF; an operator acceptance switch overrides
it through systemd's `HPR_VIDEO_TIMESTAMP_ENABLED=0|1`.

Activation requires `NTPSynchronized=yes`; `timedatectl timesync-status` must be
reviewed for recent successful sync and a small reported offset. Do not use a
reboot or set the clock manually. The implementation does not configure or restart
the Pi's time service. In the native worker the clock overlay starts silent and
is revealed only when `NTPSynchronized=yes`; it checks again every 15 seconds.
The pipeline continues without the stamp until then, without blocking HDMI,
adding queues, or restarting an encoder. A failed query hides the stamp. Worker
progress reports `timestamp_visible` so the benchmark rejects a silent trial.
Clock accuracy during a long disconnected session and this startup/re-sync
behaviour still require live validation; do not claim that a past NTP sync
proves future accuracy. Legacy FFmpeg has no dynamic NTP visibility gate and
must remain unenabled until its startup sync policy is separately validated.

## Acceptance workflow

For the deployed, default-OFF trial results and outstanding acceptance checks,
see [23 October live report](video-timestamp-live-2026-10-23.md).

First compare GitHub main, live systemd units/processes and all affected files.
The guarded updater supports the last known native deployment only; it refuses
unidentified source. Stage a committed LF Git archive, then from that stage:

```sh
sudo bash scripts/update-pi-video-timestamp.sh
```

This backs up source/config/units, installs the helper and launcher changes with
an explicit OFF drop-in, restarts ONLY `hpr-video-compositor`, and validates both
advancing streams and untouched configuration. Installing disabled source still
briefly interrupts both streams; coordinate this outside riding.
If the clockoverlay plugin is missing, the updater installs only `gstreamer1.0-x`
with no upgrades or recommends. This dependency is also included in the normal
installer so a fresh Pi has the same plugin.

Confirm `gst-inspect-1.0 clockoverlay`, `ffmpeg -filters`/drawtext and the DejaVu
font. Do not install packages during a race. Run at least 60 seconds OFF baseline,
then ON trial, then repeat OFF baseline with the same cameras, HDMI connection,
lighting/exposure, stream sizes/bitrates, receiver count and network conditions:

```sh
PYTHONPATH=/opt/hpr/gateway /opt/hpr/gateway/venv/bin/python scripts/benchmark-pi-video.py --duration 60 --latency-front-ms MEASURED --latency-rear-ms MEASURED > baseline.json
sudo bash scripts/video-timestamp-mode.sh trial
PYTHONPATH=/opt/hpr/gateway /opt/hpr/gateway/venv/bin/python scripts/benchmark-pi-video.py --duration 60 --latency-front-ms MEASURED --latency-rear-ms MEASURED > trial.json
```

`MEASURED` means real end-to-end latency measured at the SAME receiving player:
film the physical source movement/clock AND player together with a high-frame-rate
external camera, measure the frame offset, repeat and record median and worst
values for EACH stream. The already-burned timestamp alone is insufficient for
sub-second comparison. `ffprobe` connection/startup time and Pi progress age are
NOT end-to-end latency. Do not substitute them or guess a value. Confirm a short
recording and downloaded clip from BOTH streams contains the correct changing
stamp and positioning on bright and dark scenes. Validate boot/reconnect clock
behaviour and physical HDMI motion/rotation/latency as well.

The five-minute trial auto-returns OFF via a transient systemd timer. Retain the
staging directory until that rollback executes. It also disappears on reboot.
Wait for that timer before permanently enabling; the enable command refuses an
active rollback timer. Immediate safe OFF/rollback (retains source and settings):

```sh
sudo bash scripts/video-timestamp-mode.sh off
```

The JSON sample includes CPU mean/peak, BOTH publisher frame rates, stable-worker
checks, progress age, reader backpressure/timeouts, exact publisher commands and
clock status. Retain OFF/ON/OFF reports. Report EVERY measured delta; these
acceptance limits are not a claim of zero overhead. The automated gate rejects
missing latency measurements, unsynced clock, network errors, changed pipeline
settings, >5 percentage points extra overall CPU, >2% frame-rate loss, >40ms extra
median latency, or stale frame progress. Also manually reject regressions in
worst-case latency, visual quality, drops, thermals/throttling or HDMI. Measure
the existing rear-camera rate rather than assume nominal 25fps in low light.

After all checks (including boot clock handling) pass, retain evidence and run:

```sh
sudo bash scripts/video-timestamp-mode.sh enable baseline.json trial.json
```

If the operator explicitly requests enablement after being told latency remains
unmeasured, `enable baseline.json trial.json --accept-unmeasured-latency` records
that acceptance and leaves the measurements null. All other gates still apply,
including rejecting an actual measured latency regression. It does not constitute
a latency pass or a zero-overhead guarantee. The prior permanent switch is backed
up before changing it. This exception was added following Shane's renewed
instruction to proceed on 23 October, after the missing measurement was reported.

Legacy FFmpeg filter parsing is smoke-tested locally; legacy HDMI/PiP branches
still require separate integration/latency tests if used. The benchmark tool is
for the currently deployed native workers and must not be presented as validating
legacy paths. No hardware/performance/latency or central recording acceptance has
yet been completed for permanent enablement. The disabled-source deployment and
limited native trial are documented in the dated live report linked above.

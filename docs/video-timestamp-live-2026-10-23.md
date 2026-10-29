# Trike1 timestamp rollout — 23 October 2026

## State and deployment

Before installation, all four video launchers/workers in both `bin` and
`runtime/video` matched the pre-timestamp canonical source. Both cameras and
local/central RTSP were live; direct public HLS and WebRTC were also checked.
The earlier blank embedded player report was not caused by this update: the
timestamp code had not yet been installed at that point.

The Pi lacked `clockoverlay`. The guarded repository updater installed only
`gstreamer1.0-x` (no upgrades), added the shared clock helper, updated both copies
of the launchers/native worker, and created the timestamp OFF systemd drop-in.
Backup: `/opt/hpr/backups/video-timestamp-20261022T225821Z`.
The deployed runtime source was subsequently compared byte-for-byte with main.

Only `hpr-video-compositor.service` was deliberately restarted (disabled-source
installation, trial start, automatic trial rollback). No Pi reboot occurred.
The independent existing GPS recovery watchdog restarted GPS/gpsd during this
period; the video deployment did not request those actions. Other observed HPR
service PIDs remained unchanged.

The central configuration and camera-controls hashes stayed unchanged. Front
remains 800x450/30 at 1500k, with 1280x720/30 capture for HDMI; rear settings stay
640x360/25 at 700k, with 1280x720/30 capture. HDMI was disconnected throughout.

## Clock and visual checks

The Pi uses IANA `Australia/Melbourne`, currently equivalent to race-local
Australia/Sydney (including current DST). NTP was synchronised; reported offsets
were approximately -4.1 to +2.7 ms. No timezone, NTP or system-clock settings
were changed.

Both central RTSP feeds were visually inspected during the trial: a readable
bottom-left `HH:mm:ss dd-MM-yyyy` clock was present, matching current local time.
Two-second H.264 stream-copy MP4 test clips retained that stamp without any
additional encoding. These were local diagnostic recordings, NOT an acceptance
test of the TM1 recording-library download workflow.

The committed isolated GStreamer validation script used synthetic frames and
simulated clock-query answers. It verified initially-hidden -> visible -> hidden
transitions while frames kept advancing. It did not change real clock/NTP state,
touch USB devices, or restart services. A real cold-boot/NTP-outage test was not
performed (no reboot was authorised).

## OFF / ON / OFF benchmark

Each sample lasted at least 60 seconds with both native streams active:

| Metric | OFF baseline | ON trial | OFF after rollback |
|---|---:|---:|---:|
| Overall CPU mean | 42.29% | 43.93% | 41.74% |
| Front publisher fps | 29.916 | 29.613 | 29.921 |
| Rear publisher fps | 19.947 | 19.784 | 19.946 |
| Slow-reader warnings | 0 | 0 | 0 |
| Reader timeout log lines | 0 | 0 | 0 |

No worker restarts/stalls occurred inside these samples. The ON sample peak CPU
was 68.62%, versus 57.32% for the first OFF sample. No hardware throttling flags
were set; temperature after the first trial sample was 39.4 C.

Observed mean CPU increased by 1.64 percentage points against the first OFF
baseline (2.18 points against the last OFF sample). Front and rear frame rates
were approximately 1.0% and 0.8% lower during ON. Overall CPU includes unrelated
Pi activity, and these are short samples, not proof of causation or zero
overhead. Rear already delivered approximately 20 fps before the overlay despite
its configured 25fps output ceiling; do not attribute that baseline to the stamp.

## Acceptance outcome

The performance gate reported ONLY the missing real end-to-end latency
measurements for front and rear; CPU/fps/clock/network conditions passed its
configured limits. No latency values were guessed or entered as zero.

The five-minute trial automatically returned OFF and both streams passed the
normal validation afterward. At the end of that initial trial the overlay was
OFF, awaiting latency/visual acceptance. See the subsequent enablement below.

Remaining: real latency tests for both streams; physical HDMI motion/rotation/
latency; real boot/re-sync behaviour when approved; TM1 live views, recording
library and downloaded-clip acceptance; legacy FFmpeg/HDMI/PiP integration if
those rollback paths are used. No central-platform changes
were made.

## Subsequent permanent enablement — 14:48 AEDT

After the outstanding unmeasured latency was reported, Shane again instructed
the Pi chat to proceed with implementation. A dedicated explicit acceptance
option was committed/pushed before use. It permits missing latency measurements
only with operator acceptance, leaves them null, reports the limitation, and
still rejects measured latency regressions or failures of the other gates.

The affected live video files again matched main before the operation. The
repository mode script backed up the previous OFF switch, saved the permanent
`Environment=HPR_VIDEO_TIMESTAMP_ENABLED=1` drop-in under `/etc/systemd/system`,
and deliberately restarted only `hpr-video-compositor.service`. Existing GPS
watchdog activity again independently restarted GPS/gpsd during the observation.
The central config/camera controls hashes remained unchanged. Both normal video
validation and native timestamp-visible status passed. No throttling/failing
systemd units were observed at the successful post-enable check. NTP remained
synchronised (reported offset approximately -3.3 ms).

Frames fetched from BOTH central RTSP paths at 14:49 displayed the correct
bottom-left clocks (`14:49:20 23-10-2026` front, `14:49:22 23-10-2026` rear).
All 147 automated tests passed. The permanent ON switch was confirmed on the Pi
before a later follow-up SSH check timed out twice. Cause of that subsequent
reachability loss is unknown; no reboot or remedial restart was attempted.

LAST VERIFIED PERMANENT STATE: timestamps ON in both streams, persistent across
service starts/reboots; native visibility still depends on clock synchronisation.
Current live reachability/continuity after the later SSH timeouts is unverified.
End-to-end latency is still UNMEASURED, not a passed or zero-overhead result.
Physical HDMI, real boot/re-sync and TM1 library/download acceptance remain pending.

# Front-only native HDMI

The native supervisor owns a separate USB capture and RTSP publisher for each
camera. The HDMI viewer reads the front camera's local compressed MJPEG copy,
decodes, applies the central rotation, fits the entire image to the active KMS
mode and displays it. It does not consume RTSP. HDMI PiP is retired; legacy
central PiP fields are retained for compatibility but are not rendered.

Separate shared-memory rings and single-frame leaky queues prevent a frozen
HDMI reader pinning streaming buffers or slow consumers accumulating old frames. The
rear camera remains streamed. HDMI hotplug restarts only the viewer. A camera
failure restarts only that camera's capture and consumers. No HDMI and rear-only
operation still publish whichever cameras are available. Identical USB cameras
retain the existing preferred-port/fallback assignment; moving both identical
cameras together cannot establish physical front/rear identity automatically.

Workers report monotonic frame progress to `/run/hpr-video`. HDMI health uses
completed KMS render counts, not merely frames arriving before the display.
Workers get up to 15 seconds to deliver their first frames; thereafter two seconds
without progress triggers bounded termination and restart. The external supervisor also detects a stopped or hung
worker. `/run/hpr-video/video-status.json` records PIDs, frame counts and recovery
counts. This detects stalled delivery, not a camera internally repeating frames
or a faulty physical panel/cable. Physical motion and latency testing is required.

Deploy with `HPR_START_SERVICES=false scripts/deploy-pi-video-uplift.sh`, then
restart `hpr-video-compositor.service` and, if its source changed,
`hpr-trike-config-sync.service`. Deployment backs up replaced files, the service
unit and configuration under `/opt/hpr/backups/video-uplift-*`. It installs the OS
GStreamer Python bindings if absent; no OS upgrade or reboot is needed.

To roll back, revert the native-viewer source commit on GitHub main first, then
deploy that approved source using the deployment script. The old deployment
does not know about `hpr-video-native.py`; the reverted wrapper ignores it, so
there is no need to delete files manually. Keep the pre-change backup for exact
unit/configuration comparison. Validate both RTSP streams and physical HDMI.

Recovery acceptance: stop only the HDMI worker with SIGSTOP. Verify its PID
changes automatically and both publisher PIDs and frame counts continue. Also
test publisher failure, individual capture failure and a normal service restart.
Do not reboot or disconnect other USB/BLE devices as part of this test.

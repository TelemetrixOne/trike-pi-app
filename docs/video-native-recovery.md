# Stream-only native camera supervisor

The Pi opens each available USB camera once and supervises independent capture
and H.264 RTSP publisher workers. HDMI, framebuffer output, PiP, monitor hotplug
polling and the dedicated video console have been retired. The historical
`hpr-video-compositor.service` name remains to preserve service-health mappings.
There is no display worker, additional front display SHM ring, or HDMI fallback.

Each camera has `capture_size` and `capture_framerate` under `video.cameras`.
The compatibility loader migrates the old `video.display` shared capture fields
in memory, retaining per-camera overrides, then discards all display settings.
The supported full-profile synchronizer removes that block from local YAML on
its next applied profile. It never rewrites GPIO, identities or other services
as a special video migration. Central profiles must preserve the prior shared
capture mode in both cameras when removing the retired block.

The Pi-local quality file can override capture size, stream size, output frame
rate and bitrate. Camera-native capture mode must be verified separately; a
streaming output size is not proof that USB capture supports it. Matching capture
and oriented output dimensions skip scaling. Otherwise scaling preserves the
whole frame and its aspect ratio. Front rotation remains centrally controlled.

Each SHM ring and single-frame leaky queue isolates slow publishers. Workers
have 15 seconds to produce initial frames; two seconds without progress triggers
bounded recovery for that worker/camera. A camera failure must not stop the other.
Frame counters do not prove unique sensor images or end-to-end viewing latency.

## Offline preparation and pending activation (24 November 2026)

Pi SSH was unreachable. Main was compared with the clean local starting commit
82c0a0815a7929eecd86fbb455536adc1713a9cc. No live files/configuration were changed.
The source keeps the last verified native captures: both 1280x720 MJPEG/30;
front stream 800x450/30/1500k, 180-degree rotation; rear 640x360/25/700k.
Actual smaller native mode selection is deferred until Pi inspection.

Before deployment, compare GitHub main, live source/runtime copies, systemd
units/drop-ins, active commands, `/opt/hpr` and `/etc/hpr/hpr.yaml` again. Preserve
unexpected files. The scoped updater refuses unidentified source hashes and
requires reviewed SHA256 values for existing compositor/retired console units:

```sh
sudo scripts/deploy-pi-stream-only.sh --video-restart-approved COMPOSITOR_SHA CONSOLE_SHA
```

It uses the existing guarded deployment updater, preserves source/configuration
under `video-uplift-*`, and saves retired console units/tty1 mask in `stream-only-*`
under `/opt/hpr/backups`. No central YAML edits are made by the deployment script.
It removes only reviewed retired display units, unmasks an identified old tty1
mask, and restarts only video supervisor, services-health and full config-sync.
No reboot, broker/GPIO restart or camera exposure adjustment is requested.
Unknown console drop-ins or compositor overrides require separate review.

Run `scripts/validate-pi-gateway.sh`, inspect `/run/hpr-video/video-status.json`,
and visually verify both central feeds, rotation, full field of view, timestamps
and latency. Check advertised native MJPEG modes for both cameras before changing
capture sizes. Test front-only/rear-only/reconnect recovery outside a session.
Ensure other service PIDs are unchanged and no HDMI unit is enabled/active.

To restore, first revert the approved source change on GitHub main. Review and
restore the saved source/runtime files and compositor unit from the corresponding
`video-uplift-*` backup, and the reviewed retired console unit/tty1 mask from its
`stream-only-*` backup. Restore the old central display profile through supported
configuration sync if HDMI is intentionally reinstated. Restart only affected
video/configuration/health services. Backups contain configuration for comparison;
do not blindly replace current YAML, which may contain newer central changes.

# Pi Gateway Deployment

This is the intended repeatable deployment model for an HPR Raspberry Pi gateway.

For the replacement trike1 GPIO allocation and web-managed configuration push,
follow [centrally managed GPIO](centrally-managed-gpio.md). The unverified wiring
profile must be reviewed before GPIO activation.

## Single Config File

All Pi-local runtime settings live in:

```text
/etc/hpr/hpr.yaml
```

The repo example is:

```text
config/hpr.example.yaml
```

The config controls:

- Pi hostname and optional recorded Pi IPs
- trike technical ID, such as `trike1`
- display name metadata
- MQTT, Home Assistant, and Nginx endpoints
- HPR credentials
- Tailscale registration behavior
- enabled services
- stable BLE controller addresses and fallback adapter names
- BLE power/cadence scan, connection, GATT, stale-data, and reconnect timeouts
- TPMS sensor MACs and decode profiles
- GPS settings
- GPIO pins and topics
- HRM-to-rider mappings, derailleur and pedal identities
- USB camera paths and local MediaMTX settings
- health topics and intervals
- logging and service user/group

Runtime code and systemd units should not be edited for local settings.

## Direct USB Hardware

For the race build, plug the u-blox GPS receiver and external BLE dongle
directly into Pi USB ports. Do not place either device behind the removed USB
hub. The stable GPS `/dev/serial/by-id/...` path and configured Bluetooth
adapter identities remain the software interfaces; physical USB bus/port
numbers are not embedded in application code.

Run `scripts/validate-pi-gateway.sh` after moving either device. Validation must
show the stable GPS device and both controller addresses resolved to distinct
`hciN` adapters. The `hciN` values themselves may legitimately change.

## Generic Pi Paths

Use generic paths:

```text
/etc/hpr/hpr.yaml
/opt/hpr/gateway
/var/log/hpr
```

Do not use trike-specific filesystem paths such as:

```text
/opt/hpr/trike1
/opt/hpr/trike2
```

The trike ID is runtime identity, not directory structure. It is used to derive MQTT topics and hostnames, for example:

```text
hpr/trike1/pi/heartbeat
hpr/trike1/tpms/rear
hpr-trike1
```

The installer also updates Raspberry Pi cloud-init `user-data` and installs a
`preserve_hostname` rule. This prevents an older image-time name such as a
trike's display name from replacing the standard `hpr-trikeN` hostname during
the next boot.

## GPS Nav Payload

`hpr-gps.service` publishes gpsd TPV fixes to the configured GPS topic, normally:

```text
hpr/trikeN/nav
```

The payload uses the tracker contract `hpr.nav.v1`. gpsd `track` is published as `course_deg` and `bearing_deg`, gpsd `alt` is published as `alt_m`, and timing-quality metadata such as `ts_location_utc`, `ts_publish_utc`, `fix_age_ms`, `publish_latency_ms`, `accuracy_m`, and `course_accuracy_deg` is included when available. The MQTT topic trike ID and payload `trike_id` must match.

## Power/Cadence Recovery

`hpr-power-cadence.service` no longer permits indefinite BLE operations. The
publisher applies explicit timeouts to scanning, connection, GATT notification
setup, battery reads, and disconnect. A notification-staleness watchdog forces
a clean reconnect if systemd still considers the process active but pedal data
has stopped. Failed recovery attempts back off exponentially up to the
centrally configured maximum.

The installer verifies that the system Python has `venv`/`ensurepip` support.
When it is missing on Debian or Ubuntu, the installer adds the matching
`pythonX.Y-venv` package (falling back to `python3-venv`) before recreating the
gateway virtual environment.

## Clean Trike Rebuild

Create and verify a full root/boot backup first. Extract the full-platform
archive on the Pi, then run from its repository root:

```bash
sudo env HPR_CONFIRM_REBUILD=YES scripts/rebuild-pi-gateway.sh
```

The script uses the rendered `trike1` config by default. It stops and disables
known legacy publishers so two processes cannot compete for BLE or publish the
same MQTT topics. The old `/opt/hpr` tree and copies of HPR systemd/config files
are retained in a timestamped `/var/backups/hpr-pre-rebuild-*` directory. The
new services are installed, enabled, started, and validated without rebooting.

After connecting the direct USB GPS, dedicated HRM Bluetooth dongle, and both
USB cameras, run:

```bash
sudo scripts/validate-pi-gateway.sh
```

Do not delete the rollback directory until a complete hardware and dashboard
test has passed.

## USB Camera Path

Each camera publishes 640x480 H.264 to the local MediaMTX RTSP server. Stable
`/dev/v4l/by-id/...` paths prevent `/dev/videoN` renumbering from swapping front
and rear cameras. MediaMTX is supplied in the deployment archive and its SHA256
is verified before installation.

Trike 1 enables `video.display` in central configuration. The front publisher
captures the USB camera once, renders raw frames directly to the HDMI
framebuffer, and
encodes the central RTSP stream in parallel. The HDMI path has no RTSP or
network buffering and starts automatically with `hpr-video-front.service`.
The connected HDMI mode is detected from DRM on each service start. The direct
display branch scales and centre-crops to that exact resolution while the RTSP
branch retains its separately configured `stream_size`.
The local video units do not wait for internet or Tailscale, and their restart
limits are disabled so camera or display availability during boot cannot leave
the HDMI feed permanently stopped. Because this is a dedicated display, the
installer masks `getty@tty1.service` and prepares the console with its cursor,
blanking, and power-saving disabled before starting the display camera.

Each centrally managed camera can set `rotation` to `none`, `clockwise_90`,
`180`, or `anticlockwise_90`. The publisher applies the same rotation to the
RTSP stream and direct HDMI branch. If HDMI is disconnected during startup, the
publisher waits for `video.display.startup_wait_seconds` (10 seconds by default)
and then keeps the network stream running.

`video.display.picture_in_picture.enabled` selects the direct dual-camera
publisher. It captures both USB cameras at `video.display.capture_size` and
`capture_framerate`, centre-crops the main camera to fill the framebuffer, and
places the configured PiP camera at the bottom right. The PiP width and margin
are centrally configurable. Both RTSP streams are produced by the same process;
the HDMI branch never decodes a network stream.

## Tailscale

The simplest supported self-registration flow is:

1. Create a pre-authorized reusable auth key in Tailscale.
2. Prefer a tagged key such as `tag:hpr-gateway`.
3. Edit `/etc/hpr/hpr.yaml`.
4. Run:

```bash
sudo env HPR_TAILSCALE_AUTHKEY="<auth-key>" scripts/install-pi-gateway.sh
```

The installer can also prompt interactively for the key. For unattended secure installs, place the key in a root-only file and set:

```yaml
tailscale:
  auth_key_file: "/etc/hpr/tailscale.authkey"
```

Do not commit auth keys.

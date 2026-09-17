# HPR Raspberry Pi Gateway

This directory contains the repo-managed Pi gateway implementation.

Runtime-specific values belong in `/etc/hpr/hpr.yaml`. The checked-in example is `config/hpr.example.yaml`.

GPIO allocation is managed per trike in TelemetrixOne Race Configuration. See
[central GPIO configuration and migration](../docs/centrally-managed-gpio.md)
for the replacement trike1 wiring, review requirements, push/rollback protocol,
and test-card deployment procedure.

The Trike1 implementation incorporates the race-proven GPS, HRM, TPMS,
power/cadence, derailleur, GPIO, health, and USB-camera behaviour. Hardware
identities and operator values are rendered from the central race config; they
are not embedded in service code.

## Filesystem Layout

The Pi filesystem is intentionally trike-agnostic:

- `/etc/hpr/hpr.yaml` is the single runtime variables file.
- `/opt/hpr/gateway` is the default install root.
- `/var/log/hpr` is reserved for HPR logs if file logging is enabled later.

Do not create trike-specific application paths such as `/opt/hpr/trike1`. The trike identity belongs in config and is used for hostnames, MQTT topics, and MQTT client IDs.

## Deploy Shape

1. Edit `hpr-standalone-docker/config/hpr-race.yaml` on the build machine.
2. Run the central renderer and package the full-platform archive.
3. Extract that archive on the Pi.
4. Run `sudo env HPR_CONFIRM_REBUILD=YES scripts/rebuild-pi-gateway.sh`.
5. Run `sudo scripts/validate-pi-gateway.sh` after all USB hardware is connected.
6. Reboot only after validation and explicit operator approval.

The rebuild script disables known legacy publishers, preserves the previous
`/opt/hpr` tree and unit/config files under `/var/backups`, installs the rendered
`trike1` config, starts the clean services, and validates the result. It does
not reboot the Pi.

For an existing Pi that still uses legacy units for non-GPS hardware, apply the
GPS publisher and gpsd recovery uplift without replacing those other unit files:

```bash
sudo HPR_INSTALL_SCOPE=gps scripts/install-pi-gateway.sh
```

The default scope remains `all` for a fresh full-gateway installation.
The GPS maintenance scope installs only the MQTT and YAML Python dependencies
required by the publisher. The gpsd client binding comes from the maintained
operating-system `python3-gps` package and is exposed to the gateway virtual
environment with `--system-site-packages`.

The full-platform bundle also includes `scripts/deploy-pi-gps-uplift.sh` for a
backed-up GPS-only update of a live legacy Pi. Pass the full-platform archive
and the VM-rendered per-trike config to that script on the Pi.

## GPS recovery

The installer configures GPS recovery as part of the full Pi deployment:

- Connect the u-blox GPS receiver and the external BLE dongle directly to Pi
  USB ports. The race architecture no longer depends on an intermediate USB
  hub.
- gpsd USB auto-discovery is enabled with `USBAUTO="true"`.
- The generated config uses the stable u-blox `/dev/serial/by-id/...` path.
- A udev rule detects the configured GPS USB vendor/product IDs and asks
  `hpr-gps-hotplug.service` to restart gpsd after a two-second enumeration delay.
- The Python publisher requires real receiver reports before declaring recovery,
  resets its stale watchdog for each new session, and exponentially backs off
  failed reconnects up to the configured limit.
- `gpsd.socket` is enabled, and `hpr-gps.service` explicitly wants and starts
  after both `gpsd.service` and `gpsd.socket`.

For the u-blox NEO-M9N deployment, use vendor `1546`, product `01a9`, and the
stable by-id device generated from `config/hpr-race.yaml`.
The same central file controls the stale/wait timeouts, initial/maximum reconnect
delays, and whether hot-plug recovery is enabled for every generated trike config.

## BLE power/cadence recovery

The power/cadence publisher bounds BLE scanning, connection, notification
subscription, battery reads, and disconnect operations. If the Assioma stream
stops producing notifications while the process remains alive, a stale-data
watchdog tears down the connection and reconnects with capped exponential
backoff.

All scan, connect, GATT, stale, and reconnect thresholds come from
`hpr-standalone-docker/config/hpr-race.yaml` and are rendered into each
`generated/pi-configs/trikeN/hpr.yaml`. The validator also confirms that the
configured Bluetooth adapters and stable GPS device have enumerated.

## Bluetooth controller roles

The onboard controller is the telemetry role used by TPMS, pedals, and the
derailleur. The direct-attached USB Bluetooth dongle is dedicated to HRM work.
Each role is configured by Bluetooth controller address and resolved to the
current Linux `hciN` name at service startup, so USB reconnects and controller
renumbering do not swap service roles. HRM discovery stops before opening its
GATT connection, avoiding scan/connection contention on the dedicated adapter.

## USB cameras

Front and rear USB cameras connect directly to the Pi. Their persistent
`/dev/v4l/by-id/...` paths, 640x480 capture settings, and local RTSP path names
come from central config. The packaged MediaMTX binary is checksum-verified at
install time. The Pi publishes only to its local MediaMTX process; the central
VM reads `front` and `rear` over the Pi's Tailscale address.

When `video.display.enabled` is true, the configured camera publisher uses one
USB capture and tees decoded frames directly to the active HDMI framebuffer
while encoding the central RTSP stream in parallel. The HDMI path does
not traverse RTSP, MediaMTX, or any network socket. The publisher reads the
connected HDMI mode from DRM at startup and scales/crops the camera image to
that exact pixel size without stretching it. Capture and network-stream sizes
remain independently configurable.

The local MediaMTX and camera publisher units are enabled at `multi-user.target`
and have no Tailscale or internet startup dependency. The publisher waits for
the configured USB device and retries indefinitely. It gives HDMI a bounded
startup grace period, then continues publishing the network stream if no HDMI
mode or framebuffer is available. This keeps central video independent of
screen readiness after a reboot. When HDMI is ready during startup, the direct
framebuffer feed is enabled as normal; attaching it after the fallback requires
restarting that camera publisher. The installer masks the `tty1` login getty
and runs `hpr-video-console.service` before the display camera, preventing the
console's blinking cursor from being drawn over the framebuffer video.

## Tailscale

The preferred deployment path is a pre-authorized reusable Tailscale auth key, ideally tagged with `tag:hpr-gateway`.

The auth key can be supplied in one of three ways:

- `sudo env HPR_TAILSCALE_AUTHKEY=<auth-key> scripts/install-pi-gateway.sh`
- an interactive prompt during install
- a root-only file referenced by `tailscale.auth_key_file` in `/etc/hpr/hpr.yaml`

The key is used for registration only and is not written into service files.

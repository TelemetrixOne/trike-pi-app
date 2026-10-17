# Permanent Pi-local video quality

The explicit local-quality exception uses `/etc/hpr/video-local.yaml`, separately
from central race/trike configuration and USB picture controls. Current front:

```yaml
front:
  stream_size: 1280x720
  output_framerate: 30
  bitrate: 2000k
```

Rear is omitted and keeps its central settings (currently 640x360, 25 fps, 700k).
The approved profile is `config/video-local.front-1280x720.yaml`. Apply future
quality profiles with `scripts/apply-pi-local-video.sh PROFILE.yaml`; it merges only
specified quality fields, preserves unmentioned roles and settings, backs up the
current file and automatically restores it if validation fails. The earlier
800x450/25/1500k profile remains in the repository as a historical alternative.
Front device identity, rotation, stream path, credentials, capture geometry and
HDMI configuration remain central; only quality fields listed above are local.
No web configuration section or Home Assistant changes are needed.

The normal video launcher reads central settings through the existing loader,
then applies local quality. This persists through service starts, reboot and
normal deployments: the installer creates an empty example only if no local
file exists. Other/new trikes therefore receive no unsolicited overrides.
Central synchronisation does not own or overwrite the local file. A centrally
disabled camera is not enabled by a local quality setting.

Edit with `sudo nano /etc/hpr/video-local.yaml`, then apply with:

```sh
sudo systemctl restart hpr-video-compositor.service
```

That briefly interrupts both streams. It does not restart the other Pi services.
Supported sizes are 640x360, 800x450, 960x540 and 1280x720; integer frame rate must
not exceed capture FPS; bitrate accepts 100k..8M, such as 1500k or 1.5M. Front and
rear sections are supported, but only front is overridden in this installation.
Unknown keys, duplicates, malformed YAML or invalid values are rejected as a whole.
An unreadable/invalid optional file logs an error and falls back to central
settings so it cannot prevent both cameras from streaming.

Migration uses `scripts/deploy-pi-local-video.sh`, guarded against unidentified live
files. It preserves the successful trial and configs in a protected backup, moves
the runtime-only trial drop-in/state into that backup, restores normal video
startup and restarts only `hpr-video-compositor.service`. It automatically restores
the prior working trial if deployment validation fails. There is no dedicated
operator rollback workflow required for the permanent local configuration.
Use `scripts/validate-pi-local-video.py` with the installed gateway Python and
`PYTHONPATH=/opt/hpr/gateway` to check effective quality and advancing video workers.

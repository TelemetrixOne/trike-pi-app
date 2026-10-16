# Local USB-camera picture tuning

Edit on the Pi:

```sh
sudo nano /etc/hpr/camera-controls.yaml
```

There are separate `front` and `rear` sections. Save integer picture-control values;
the existing video service reloads within a few seconds without restarting feeds.
Controls are applied to the resolved camera devices and reapplied after USB
reconnection or service startup. The file survives deployments and is deliberately
separate from centrally synchronised `/etc/hpr/hpr.yaml`. Camera identities,
rotation, resolution, frame rates and streaming remain centrally configured.

The example file documents the current cameras' ranges. Start with small changes
to `brightness`, `contrast`, `gamma`, `saturation` or `sharpness`. Automatic exposure
(`auto_exposure: 3`) and white balance (`white_balance_automatic: 1`) are the baseline
for changing outdoor light. These cannot guarantee usable night exposure or a
particular exposure-related frame rate; test real riding conditions.

Manual exposure requires **both** `auto_exposure: 1` and `exposure_time_absolute` in
that camera's section (100 microsecond units). Manual colour temperature requires
`white_balance_automatic: 0` and `white_balance_temperature`. Values and menu choices
are validated against the actual camera before writes. Unsupported or out-of-range
profiles fail for that camera without stopping either feed. Partial driver failures
attempt to restore changed settings on the same device generation.

Invalid YAML retains the last good profile and reports an error. Removing a control
stops managing it but does not reset its current hardware value; use an explicit
value to restore it. Removing the file retains the last good in-memory profile until
the service is restarted. If a camera is absent, the other remains independent.
Duplicate camera/control keys are rejected rather than silently replacing a value.

For specifically bright sunshine, `config/camera-controls.sunny.example.yaml`
provides fixed 1/10000-second exposure (`auto_exposure: 1`,
`exposure_time_absolute: 1`), minimum gain and the inspected local brightness -30.
This is a trial preset, not a universal day/night setup. These cameras expose no
automatic exposure-bias control, so this preset necessarily disables automatic
exposure. It can be too dark in shade and is unsuitable for dusk/night without
retuning. To restore automatic exposure, set `auto_exposure: 3` and **remove**
`exposure_time_absolute` in each section; do not leave both in conflict.
The supported `scripts/apply-pi-camera-profile.sh PROFILE.yaml` validates controls,
backs up local/central configuration and applies a profile through hot reload,
without restarting video. It restores the prior local file if validation fails.

Inspect the `camera_controls` section:

```sh
python3 -m json.tool /run/hpr-video/video-status.json
journalctl -u hpr-video-compositor.service -n 30 --no-pager
```

Deployment uses `scripts/deploy-pi-camera-controls.sh`. It guards both existing
native-video files against unexpected changes, backs them up and preserves any
existing local tuning file. Only the video compositor is restarted once to install
the worker. Future edits need no service restart. The normal installer creates
the example only if the local file does not already exist.

Validate with `scripts/validate-pi-camera-controls.py`; `--test-reload` briefly changes
front brightness by one unit and checks automatic restoration with stable video
PIDs. Run that option with sudo when safe to test. Revert with:

```sh
sudo bash /opt/hpr/backups/camera-controls-TIMESTAMP/rollback.sh /opt/hpr/backups/camera-controls-TIMESTAMP
```

Rollback restores the prior video software/local file, preserving edited tuning
in the backup. It does not reset hardware picture values. The initial default
profile matches the inspected cameras, so initial deployment changes no picture
values. No reboot, central-configuration edit or non-video service restart is needed.

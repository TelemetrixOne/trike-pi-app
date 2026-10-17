# Temporary local front-stream test

This explicitly operator-approved exception to central streaming settings changes
only front publisher `stream_size` and `bitrate` in memory. Default trial:
800x450, 1500k, retaining the current central frame rate, rotation and stream path.
Rear, USB capture, HDMI geometry and camera picture settings are not changed.

Install using `scripts/deploy-pi-video-stream-trial.sh`, then on the Pi:

```sh
sudo /opt/hpr/gateway/venv/bin/python /opt/hpr/gateway/bin/hpr-video-stream-trial.py start
```

Quick rollback:

```sh
sudo /opt/hpr/gateway/venv/bin/python /opt/hpr/gateway/bin/hpr-video-stream-trial.py stop
```

Start and stop each briefly interrupt both camera streams because the existing
supervisor loads settings only at startup. Only `hpr-video-compositor.service`
is restarted. The helper checks advancing frames on all previously present video
workers and front publisher arguments; failure attempts immediate rollback.
It is not an end-to-end latency measurement or a continuous overload watchdog.

The trial uses a narrowly scoped ExecStart drop-in under `/run/systemd/system/`
and a non-secret state file under `/run/hpr-video-stream-trial/`. Central
`/etc/hpr/hpr.yaml` and Race Configuration are never edited; every video-service
start reads current central settings before applying the two front overrides.
Stop removes only the helper's exact drop-in and returns to current central
settings (recorded baseline is shown in the trial state). If central settings
change during the trial, rollback follows the new central values, not an old copy.
The override disappears on reboot; no reboot is needed or performed by this tool.

Original config, unit and progress snapshots are preserved in a protected
`/opt/hpr/backups/video-stream-trial-TIMESTAMP` folder. Config snapshots may contain
credentials and must not be displayed or shared. Runtime state holds no credentials.
Existing unknown trial files or drop-ins are never replaced. Native video source,
camera allocation, BLE and other services are untouched.

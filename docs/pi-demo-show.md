# Pi on-demand demo

`scripts/deploy-pi-demo-show.sh` removes the former HDMI splash service and
graphic from the Pi, restoring the front-only camera viewer at boot and during
the demo. Camera captures and streaming publishers remain separate workers.

Lights, horn, and DRS **never run on boot**. The separate
`hpr-demo-show.service` listens for an explicit, non-retained MQTT request:

From the Pi console, run the local Python command (no web button required):

```sh
/opt/hpr/gateway/venv/bin/python /opt/hpr/gateway/bin/hpr-demo-sequence.py
```

Type `YES` at the prompt. The command uses `/etc/hpr/hpr.yaml` for the broker,
credentials, topic root and trike identity, generates a fresh UUID, publishes
with QoS 1 and retain **false**, and waits for the listener's result. It exits
nonzero for rejection, failure or timeout. `--yes` skips only the console
confirmation; it does not bypass the listener's state checks or restoration.
Install or restore this command with `scripts/deploy-pi-demo-trigger.sh` and
`scripts/rollback-pi-demo-trigger.sh`. Installation does not trigger the show or
restart any service.

| Purpose | Topic | Payload |
| --- | --- | --- |
| Start | `{topic_root}/{trike_id}/control/demo/request` | `{"action":"START","request_id":"<new UUID>"}` |
| Status | `{topic_root}/{trike_id}/control/demo/status` | JSON with `state`, `request_id`, and optional `reason` |

The Pi publishes `ready`, `running`, `completed`, `rejected`, or `failed`
states. The Pit Operations button should show an "Are you sure?" prompt,
generate a fresh UUID, publish
with QoS 1 and retain **false**, disable itself while `running`, and display
the status/rejection reason. Duplicate UUIDs and retained commands are ignored.
The central Race Configuration still supplies the broker, topic root, trike
identity, output mapping, DRS calibration, and rider-switch assignment.

The Pi accepts a demo when it has complete GPIO state and the momentary horn
is idle. It does not require GPS, stationary confirmation, or a particular
physical-switch position. It snapshots the headlight mode/requested state,
auxiliary-light requested state, and DRS mode. Lights and DRS modes can be
temporarily overridden. It requests a one-second blackout first, checking
the headlight's effective GPIO state and its separate feedback input are OFF.
If the headlamp feedback stays ON, the demo fails before any horn or DRS action
and restores the original settings. A nominal ten-second sequence mixes an
ignition light chase, eight syncopated fixed-pitch horn hits, alternating light
responses, two spaced DRS open/close movements and a combined-light finale.
DRS direction changes remain at least two seconds apart. It closes with all demo lights and
the horn off before restoring the original headlight, auxiliary-light, and DRS
modes. Timing is best-effort because each command waits for an acknowledgement;
the restore phase starts at 9.2 seconds and completion is nominally at ten seconds.
Slow acknowledgements or restoration faults can extend this; safe restoration
takes priority over a hard time cutoff. An error aborts the show and still
runs restoration. A physical switch may continue to force an output (including
auxiliary lights), and an already-active momentary horn
pulse cannot be restored to its exact remaining duration. There is no direct
GPIO access in the demo listener. **The operator remains responsible for
running the demo only when the trike is stationary and clear of people.**

The HDMI viewer stays on the live front camera throughout the demo. There is
no startup graphic, display override, or HDMI dependency for the sequence.

Deployment backs up replaced files and starts only the demo listener. If the
native HDMI worker source changed, it restarts the video compositor once to
load that source; this briefly reconnects the independent stream publishers.
It does not reboot or restart GPIO. Roll back using the exact
backup path printed by deployment:

```sh
sudo scripts/rollback-pi-demo-show.sh /opt/hpr/backups/demo-show-<timestamp>
```

The first physical demo should be supervised. Without an approved trigger,
validation is limited to unit status, MQTT `ready` status, configuration,
HDMI frame rendering, and live camera/service health; no horn, lights, or DRS
are actuated for software testing.

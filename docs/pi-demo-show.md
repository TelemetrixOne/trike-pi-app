# Pi HDMI splash and on-demand demo

`scripts/deploy-pi-demo-show.sh` installs the supplied Ashton Martin graphic
as an aspect-correct HDMI splash. It is drawn once to the 16-bit framebuffer
during video-console startup; the existing front-only native camera viewer
takes over when ready. The splash does not alter camera capture or streams.

Lights, horn, and DRS **never run on boot**. The separate
`hpr-demo-show.service` listens for an explicit, non-retained MQTT request:

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
temporarily overridden. It requests a 1.5-second blackout first, checking
the headlight's effective GPIO state and its separate feedback input are OFF.
If the headlamp feedback stays ON, the demo fails before any horn or DRS action
and restores the original settings. A roughly 20-second cue schedule then
mixes alternating and paired light flashes, eight varied fixed-pitch horn pulses,
and two spaced DRS open/close movements. It closes with all demo lights and
the horn off before restoring the original headlight, auxiliary-light, and DRS
modes. Timing is best-effort because each command waits for an acknowledgement;
the restore phase is reserved at the end. An error aborts the show and still
runs restoration. A physical switch may continue to force an output (including
auxiliary lights), and an already-active momentary horn
pulse cannot be restored to its exact remaining duration. There is no direct
GPIO access in the demo listener. **The operator remains responsible for
running the demo only when the trike is stationary and clear of people.**

Deployment backs up replaced files and starts only the demo listener; it
does not reboot or restart GPIO/video services. Roll back using the exact
backup path printed by deployment:

```sh
sudo scripts/rollback-pi-demo-show.sh /opt/hpr/backups/demo-show-<timestamp>
```

The first physical demo should be supervised. Without an approved trigger,
validation is limited to unit status, MQTT `ready` status, configuration,
HDMI frame rendering, and live camera/service health; no horn, lights, or DRS
are actuated for software testing.

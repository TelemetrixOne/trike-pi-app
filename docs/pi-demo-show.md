# Pi HDMI splash and on-demand demo

`scripts/deploy-pi-demo-show.sh` installs the supplied Ashton Martin graphic
as an aspect-correct HDMI splash. It is drawn once to the 16-bit framebuffer
during video-console startup; the existing front-only native camera viewer
takes over when ready. The splash does not alter camera capture or streams.

Lights, horn, and DRS **never run on boot**. The separate
`hpr-demo-show.service` listens for an explicit, non-retained MQTT request:

| Purpose | Topic | Payload |
| --- | --- | --- |
| Start | `{topic_root}/{trike_id}/control/demo/request` | `{"action":"START","request_id":"<new UUID>","stationary_confirmed":true}` |
| Status | `{topic_root}/{trike_id}/control/demo/status` | JSON with `state`, `request_id`, and optional `reason` |

The Pi publishes `ready`, `running`, `completed`, `rejected`, or `failed`
states. The Pit Operations button must require an operator confirmation that
the trike is stationary and clear of people, generate a fresh UUID, publish
with QoS 1 and retain **false**, disable itself while `running`, and display
the status/rejection reason. Duplicate UUIDs and retained commands are ignored.
The central Race Configuration still supplies the broker, topic root, trike
identity, output mapping, DRS calibration, and rider-switch assignment.

The Pi accepts a demo only when it has complete GPIO state and the horn is
idle. A fresh valid GPS report above 1 km/h vetoes or aborts the demo; lack of
an indoor GPS fix does not override the operator's physical confirmation. It snapshots the
headlight mode/requested state, auxiliary-light requested state, DRS mode, and physical DRS
switch. Lights and DRS modes can be temporarily overridden. It checks GPS and
the rider switch throughout. It flashes existing auxiliary/headlights, plays
a short-short-long fixed-pitch horn rhythm, opens then closes DRS, and restores
the original headlight, auxiliary-light, and DRS modes. Missing GPS, changing
switch state, or motion aborts the show and still runs restoration. A physical
switch may continue to force an output, and an already-active momentary horn
pulse cannot be restored to its exact remaining duration. There is no direct
GPIO access in the demo listener. **Do not use
the button as a substitute for physically confirming the trike is stationary
and clear of people before moving DRS.**

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

# Pi startup show

`scripts/deploy-pi-startup-show.sh` installs the supplied Ashton Martin image
as a best-effort, aspect-correct HDMI splash. It leaves the existing front-only
native viewer and both camera publishers unchanged. The image is drawn once to
the 16-bit framebuffer during the video-console boot dependency, then the
native camera viewer takes over its KMS plane. No display means no splash.

The separate `hpr-startup-show.service` uses the existing GPIO controller's
MQTT commands; it never opens GPIO pins itself. It runs at most once per boot,
within the first 90 seconds, only if the headlight, auxiliary light, and horn
all report idle. It alternates the two lights twice, sounds a short-short-long
fixed-pitch horn rhythm, then restores the headlight's prior AUTO/OFF mode and
turns the other outputs off. If the broker or state is unavailable, it skips
the flourish. DRS is deliberately not moved at boot: it remains under the
rider's physical switch and central control profile.

The deployment makes no service restart and does not reboot. It prints a
backup directory. To disable the sound and lights without affecting video,
run `sudo systemctl disable --now hpr-startup-show.service`. To remove the
entire addition, run `sudo scripts/rollback-pi-startup-show.sh
/opt/hpr/backups/startup-show-<exact timestamp>` from the deployed repository.
The rollback does not restart the video or GPIO services.

Validation: run `sudo scripts/validate-pi-gateway.sh`, inspect
`systemctl status hpr-video-splash.service hpr-startup-show.service`, and after
the next approved normal power-up, visually check image-to-camera handoff and
the brief output pattern. Do not reboot solely to test the show without
explicit user approval.

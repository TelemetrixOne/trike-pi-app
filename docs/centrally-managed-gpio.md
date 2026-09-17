# Centrally managed Pi GPIO

The 2026-09-16 trike1 allocation replaces the previous GPIO allocation. It is
staged in the central race configuration and generated Pi profile. On 2026-09-17
the replacement GPIO mapping was also written to the live Pi configuration,
with GPIO kept disabled. The Pi runtime and configuration-sync service were
subsequently installed and verified on the same day. Sync is enabled and
publishes fresh MQTT status; physical GPIO remained disabled until the relay
polarity and hardware review were completed. The Race Configuration web release
was deployed and passed health checks on the same day.

## Ownership

Race Configuration owns `trikes.<trike_id>.pi.enabled` and the corresponding
`gpio` mapping in `hpr-standalone-docker/config/hpr-race.yaml`. Pi and GPIO are
separate capabilities. Only trike1 is assigned the new hardware profile; other
trikes do not inherit it. Operators edit BCM numbers, relay polarity, boot states,
feedback inputs, I2C assignments and the reserved DRS servo pin in the web editor.

The installed Pi still uses one runtime file, `/etc/hpr/hpr.yaml`, and generic
code in `/opt/hpr/gateway`. No trike-specific runtime directory is introduced.
The `generated/pi-configs/trikeN` directories contain deployment artifacts only.
The new sync service changes only `gpio`, `services.gpio_control.enabled`, and
`configuration.gpio_hash`. Network settings, credentials and other services are
preserved. Whole-platform configuration reconciliation remains separate work.

The web release's normal, backed-up config migration stages the replacement for
trike1 if it has no GPIO `profile_version`. It preserves ADC calibration and all
other trikes. Once a profile version exists, subsequent restarts preserve operator
edits. The migration never publishes a device update. Central history retains the
previous allocation for review/rollback; no legacy source files are deleted.

### Web Release Deployment (2026-09-17)

The live TelemetrixOne web container was replaced with image
`telemetrixone-website:gpio-race-config-de37a71`, built from committed release
`de37a71`. Its predecessor was retained as
`telemetrixone-website:rollback-gpio-race-config-20260916T233400Z`.

Before the switch, the deployment captured a checked runtime archive containing
the SQLite database, active race configuration and configuration history:
`backups/telemetrix-runtime-20260916T233423Z.tar.gz` on the central VM. Its
SHA256 companion file verified successfully. A separate deployment backup also
preserved the prior `.env` image selection. The new container reached Docker
health `healthy`; `/healthz` reported healthy configuration, database, disk,
MQTT and outbox checks. Recent application logs had no errors, tracebacks or
critical messages.

The live central configuration now has trike1 GPIO profile version `2026-09-16`,
with the four output names `headlight`, `aux_lights`, `demister` and `horn`.
At that point its review state was `UNKNOWN_REVIEW_REQUIRED`. The protected GPIO
API route is present and returns `401` without authentication, as intended. No
desired profile was published to MQTT and no actuator was activated during that
release.

To revert the application image, restore the prior `.env` image selection and
run `docker compose up -d --no-deps --no-build telemetrix-web`. To revert both
the application and its runtime/configuration data, restore the verified runtime
archive with `scripts/restore-telemetrix-runtime.sh` on the central VM.

### Transparent Header Logo Release (2026-09-17)

The same web-only deployment process released image
`telemetrixone-website:transparent-headers-df441bd`. It changes only the header
logo source: Race Configurator, Race Timing, trike operations, Users and the
shared spectator portal header now load `telemetrixone-logo-transparent.png`.
Their existing CSS dimensions remain unchanged. The previous GPIO/Race
Configuration image is retained as
`telemetrixone-website:rollback-transparent-headers-20260916T234500Z`.

The pre-release runtime archive
`backups/telemetrix-runtime-20260916T235041Z.tar.gz` verified successfully. The
replacement container reached Docker health `healthy`; live container source
checks confirmed each requested page uses the transparent image or the updated
shared portal header.

### Confirmed Trike1 GPIO Activation (2026-09-17)

The confirmed relay polarity was saved through the live Race Configuration data
store: headlight BCM5 and horn BCM17 are active-low; aux lights BCM24 and
demister BCM25 are active-high. All four use an explicit `"OFF"` boot default.
DRS remains disabled, with BCM12 reserved and no PWM frequency or pulse values.

The live web container runs
`telemetrixone-website:gpio-polarity-69c452e`, built from commit `69c452e`.
The previous web image is retained as
`telemetrixone-website:rollback-gpio-polarity-20260917T001200Z`; the pre-change
runtime archive is `backups/telemetrix-runtime-20260917T000817Z.tar.gz` on the
central VM, with SHA256 `f43dc0bb34cd862b0625c0d95abdbe16c3ccb883da4be15ba61e8787a008fde6`.

Race Configuration published the desired profile and the Pi acknowledged
`state=applied` with hash
`c66066920a636ff610ad1042602e6b39734d25fcf6d3ce5d987c0a4c678f8af9`.
`hpr-gpio-config-sync.service`, `hpr-gpio-control.service`, and
`hpr-trike1-health-agent.service` are active and enabled. This does not enable
DRS servo actuation.

### Relay Polarity Correction and ADC Check (2026-09-17)

Follow-up hardware testing corrected the trike1 relay polarity to BCM5
headlight active-high, BCM24 aux lights active-low, BCM25 demister active-low,
and BCM17 horn active-low. The live Race Configuration profile was pushed and
the Pi acknowledged `state=applied` with hash
`a653748ceacaf7a157465599d5819c86fd6291528ee9ba2937121202de368bf3`.

The Pi exposes `/dev/i2c-1` and an I2C bus scan detects an address responder at
`0x48`, matching the configured ADS1115 address. A direct ADS1115 initialisation
write and the running GPIO service both return I2C errors (`Errno 5` and
`Errno 121`), so no ADC channel values are currently available. This is
`UNKNOWN_REVIEW_REQUIRED`: verify the ADC module type, power/ground, SDA/SCL
wiring, address strap, and signal integrity before changing ADC calibration or
runtime settings.

## Trike1 allocation

| Physical pin | BCM GPIO | Function | Connection |
| --- | --- | --- | --- |
| 1 | - | 3.3 V supply | Horn LH module and ambient sensor pull-up/high side |
| 3 | 2 | I2C SDA | ADC data, ambient sensor and current shunt |
| 5 | 3 | I2C SCL | ADC clock |
| 9 | - | Ground | ADC ground |
| 11 | 17 | Horn output | Relay IN4 |
| 13 | 27 | Headlamp feedback | 75k/25k divider |
| 14 | - | Ground | ADC ADDR strap |
| 15 | 22 | Aux light feedback | 75k/25k divider |
| 16 | 23 | Demist feedback | 75k/25k divider |
| 18 | 24 | Aux lights output | Relay IN3 |
| 22 | 25 | Demist output | Relay IN2 |
| 29 | 5 | Headlamp output | Relay IN1 |
| 31 | 6 | DRS switch input | Switch to ground, internal pull-up, active low |
| 32 | 12 | Reserved DRS servo PWM | Inactive until calibration and behaviour are reviewed |

All relay outputs have OFF boot defaults. The confirmed trike1 polarity is BCM5
headlight active-low, BCM17 horn active-low, BCM24 aux lights active-high and
BCM25 demister active-high. The managed profile is eligible for activation. The
sync service remains enabled to deliver later configuration changes.

## Hardware review

Relay polarity and the non-DRS hardware review are confirmed. DRS servo
calibration and DRS switch-to-servo behaviour remain `UNKNOWN_REVIEW_REQUIRED`.
GPIO12 is reserved and conflict-checked even though servo actuation is disabled.
This release does not implement servo motion. GPIO6 publishes switch state; it
does not move the servo.

The 75k/25k divider produces `Vgpio = Vsupply / 4`. Confirm the maximum switched
supply, including transients, before wiring feedback to the Pi. For example,
20.6 V would produce 5.15 V and is unsuitable for a 3.3 V GPIO input. The supply
voltage on these circuits has not been measured. See the
[Raspberry Pi voltage specifications](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#voltage-specifications).

The existing ADC driver uses I2C bus 1 on BCM2/BCM3. The editor rejects alternate
bus/pin combinations that the current driver cannot implement. Verify that the
OS exposes `/dev/i2c-1` and that the ADC and its existing calibration are correct.
The software acknowledgement verifies config application and GPIO device startup;
it does not certify electrical wiring, ADC readings or physical actuator motion.

## Push and acknowledgement

1. Save the central Race Configuration. Review/polarity omissions may be saved as
   drafts, but an enabled profile with unresolved hardware review cannot be pushed.
2. Select **Push saved GPIO** for the selected Pi-equipped trike. Unsaved edits
   and stale browser revisions are rejected. Administrator access is required.
3. The web service publishes a retained, QoS 1, secret-free desired profile on
   `<topic_root>/<trike_id>/config/gpio/desired` using the configured broker.
4. `hpr-gpio-config-sync.service` validates identity, schema, hash, BCM numbers,
   pin conflicts, capability flags and supported hardware. It backs up the
   installed config into `/etc/hpr/gpio-history`, atomically replaces the file,
   and enables/restarts or disables only `hpr-gpio-control.service`.
5. The GPIO unit uses systemd notification so restart completion follows GPIO
   device creation. A failed restart restores the previous file and service
   enabled state. An interrupted transaction is recovered on sync-service startup.
6. The Pi reports its applied hash on
   `<topic_root>/<trike_id>/config/gpio/status`. The web editor shows a matching
   apply only after a fresh acknowledgement. A broker publish alone is pending.
   Retained-only, mismatched, expired or offline reports cannot establish readiness.

The Pi repeats status every 10 seconds and verifies the file and service state.
The web side expires confirmation after 30 seconds by default. These intervals
are configurable through `services.gpio_config_sync.status_interval_seconds` on
the Pi and `web_platform.telemetry.gpio_config_stale_seconds` centrally. Identical
profiles do not restart a healthy service. Broker outages leave the local config
and GPIO loop running; retained delivery permits a later reconnect.

The sync service needs root to atomically replace the protected configuration and
manage the one fixed GPIO systemd unit. It accepts no remote command, path or unit
name. Backups are root-only because the full local file contains credentials.
Transport uses the existing authenticated MQTT connection and shared credential
policy. Per-device ACLs/signatures and whole-race readiness enforcement are not
introduced by this change.

## Topic migration

The following topic changes accompany the replacement hardware profile. No broker
retained messages or Home Assistant entities are automatically deleted.

| Existing topic suffix | New behaviour |
| --- | --- |
| `control/headlight/*` | Preserved; BCM5 remains headlamp control |
| `control/demister/*` | Preserved; output moves from BCM19 to BCM25 |
| `control/horn/*` | Preserved; output moves from BCM13 to BCM17 |
| `control/tail_light/*` | Retired from this profile; never aliased to aux lighting |
| `control/aux_lights/*` | New aux lighting control, BCM24 |
| `input/headlamp/state` | Preserved; feedback moves from BCM17 to BCM27 |
| `input/taillamp/state` | Retired from this profile |
| `input/spare_headlamp/state` | Retired; `input/aux_lights/state` now represents BCM22 |
| `input/spare_taillamp/state` | Retired; `input/demister/state` now represents BCM23 |
| `input/drs_switch/state` | New BCM6 switch state, ON when grounded |
| `control/drs/*` | Remains disabled pending calibrated actuator implementation |

All suffixes have the configured `<topic_root>/<trike_id>/` prefix. Review old
Home Assistant/dashboard consumers and retained discovery entries during a
separately approved migration. They remain `UNKNOWN_REVIEW_REQUIRED` until checked.

## Deployment and rollback proposal

These are proposed deployment actions, not commands run on production. The
source Pi requires explicit approval before any of them is executed.

| Proposed action | Purpose | Rollback |
| --- | --- | --- |
| Deploy the pinned web release through the existing deployment procedure | Install editor/API and stage backed-up central profile | Restore previous image/release and the central history snapshot |
| Run `sudo scripts/install-pi-gateway.sh` from the pinned repository checkout on the test SD card | Install the GPIO sync module and notify-capable GPIO unit | Power down and return to the untouched working SD card |
| Push the verified trike1 profile using Race Configuration | Apply the new wiring through the managed path | Automatic restore on startup failure; use the preserved SD card for hardware rollback |
| Reboot the test SD card with `sudo reboot` | Confirm persisted configuration and enabled-service recovery | Return to the untouched working SD card |

The existing installer affects more than GPIO and must not be run casually on the
working card. Before any live-card upgrade, capture a fresh read-only inventory,
review the exact installer diff and config, and obtain approval for its full scope.
Restoring an older central profile alone does not revert a Pi; it must be explicitly
pushed and accepted. A physical wiring change also requires the matching wiring
rollback. Never run the old GPIO allocation against the new wiring.

## Test procedure

Use the same Pi with a separate microSD card, leaving the working card untouched.
Start with controlled/disconnected loads. Generate the trike1 Pi config from the
central file, install it as `/etc/hpr/hpr.yaml`, and run the installer on that card.
Verify the broker and I2C configuration, then confirm hardware review in Race
Configuration and push. Check matching acknowledgement, physical OFF boot states,
each relay, each feedback input, horn pulse expiry and DRS switch state. Verify
GPIO12 remains un-driven. Run `sudo scripts/validate-pi-gateway.sh`, reboot, and
confirm service recovery. Exercise MQTT disconnection/reconnection. Only then
propose migration of the working card.

Local automated validation covers profile rendering, duplicate/conflicting pins,
wrong-trike/hash rejection, idempotency, disable, rollback, interruption recovery,
API permissions/revision checks, broker-offline errors, status freshness and mocked
GPIO input polarity. The browser check covers editing/saving/pushing and capability
gating at 1440px and 390px. Real Linux systemd, actual MQTT transport, electrical
hardware and reboot recovery still require the test-card run.

### Recorded validation (2026-09-17)

| Check | Result |
| --- | --- |
| `python -m unittest discover -s hpr-standalone-docker/services/telemetrix-web/tests` | PASS: 103 tests |
| `python -m unittest discover -s pi-gateway/tests` | PASS: 20 tests |
| `python -m unittest discover -s hpr-standalone-docker/tests -p test_pi_config_contracts.py` | Two renderer checks PASS; existing installer text assertion FAIL |
| `node hpr-standalone-docker/services/telemetrix-web/tests/check_gpio_configurator.cjs` | PASS: 1440px and 390px, offline API fixtures, screenshots inspected |
| `bash -n scripts/install-pi-gateway.sh scripts/validate-pi-gateway.sh` | PASS |
| `git diff --check` | PASS |
| Real Pi/systemd/MQTT/reboot | NOT_RUN: no production deployment authorised in this change |

Tests ran with the existing local Python dependencies, plus isolated GPIOZero,
HTTPX and BLE test dependencies. The browser check needs Playwright; its
`HPR_TEST_PYTHON`, `HPR_TEST_BROWSER` and `HPR_TEST_ARTIFACTS` variables select
the Python executable, browser channel and optional screenshot directory.

The existing failing installer test expects the literal
`venv --clear --system-site-packages`, but the installer uses
`venv --system-site-packages`. The same test was rerun against the unmodified
HEAD installer and failed identically. No venv-clearing behaviour was added to
satisfy this unrelated assertion. Review that existing installer/test mismatch
separately before claiming the entire repository test suite passes.

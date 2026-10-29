# GPIO reliability correction, 2026-10-12

Read-only comparison before modification confirmed GitHub main `c2c792f` matched
the live GPIO controller, profile validator and both configuration synchronisers.
Units, active processes, `/opt/hpr`, runtime GPIO configuration, timers and cron
were inspected. Imported versions were gpiozero 2.0.1, ADS1x15 3.0.5 and Paho 2.1.0.
The full-trike and GPIO-only desired profiles contained identical GPIO settings.

## Measured cause

During a 30-second idle sample, GPIO6 (the configured DRS switch input) generated
246,758.6 interrupts/second. GPIO control consumed 59.9% of one CPU core, with
the gpiozero/lgpio input-processing thread responsible for nearly all of it.
The corresponding kernel IRQ thread was also heavily active. The three other
feedback IRQ counters did not advance during the sample.

MQTT commands were quiet: the observed `/set` messages were initial retained
deliveries. Actual telemetry averaged about three messages/second. The ADC was
returning readings. Thus the measured dominant cause is the DRS input interrupt
storm, rather than an idle MQTT command loop or a stuck ADC conversion.
The source of the electrical/kernel-level repeated edges remains unverified.

## Implementation

- Plain gpiozero `InputDevice` inputs are sampled and debounced without kernel
  edge subscriptions. The default control tick is 20 ms, with 50 ms input
  debounce. The configured pull and active level remain unchanged.
- The main control loop owns actuator state. MQTT callbacks enqueue requests
  into a bounded queue and return promptly; input changes are handled there too.
- Relay writes occur only when effective state changes. Changes and command
  acknowledgements are published; five-second GPIO health evidence is retained.
- DRS movement uses timer updates instead of callback sleeps. An identical
  completed/in-progress command does not repeat movement or extend PWM release.
  Central calibration, transition time and release policy remain authoritative.
- All retained transient actuator commands are ignored on subscription/reconnect.
  This prevents the old retained CLOSE command from overriding the central
  RIDER_CONTROL startup mode. A fresh live command remains supported.
- ADC sampling runs in one worker, with sleeping readiness checks and a 100 ms
  conversion deadline. Kernel I2C stalls cannot block the control loop. Stale or
  failed light readings are unavailable to AUTO, which holds its previous state
  until fresh data returns. ADC freshness is included in GPIO health evidence.
- Disconnected publishing is skipped and Paho's queue is bounded to 128 messages.
  Reconnect publishes current GPIO states and fresh telemetry snapshots.
- Profile validation rejects zero/negative/non-finite timing and invalid moving
  averages. No central pin assignment, polarity or calibration was changed.

No video code or BLE architecture was changed. The
configuration synchronisers' shared-writer risk identified in the offline audit
was not the measured fault: live GPIO contents agreed, and no restart loop was
observed. Resolving revision ordering still requires central producer coordination.

## Deployment and rollback

Use `scripts/deploy-pi-gpio-reliability.sh` from the committed GitHub-main source.
It refuses unexpected live modules, backs up both modules, config and unit text,
then replaces only `services/gpio_control.py` and `gpio_profile.py`. It restarts
GPIO and the two already-running config synchronisers to load the validator.
No unit file or runtime config is overwritten, and no reboot is performed.

Use `scripts/rollback-pi-gpio-reliability.sh` with the exact printed backup path
to restore the modules. It preserves any subsequent central config edits.

## Validation

Local suite: 76 tests passed, including no edge subscriptions under noisy input,
stable-sample debounce, serial command ownership, retained-command rejection,
nonblocking DRS transitions/release, duplicate command idempotency, bounded ADC
waits, independent ADC startup, stale-light handling, disconnected publishing,
timing validation and horn expiry independent of ADC work.

The operator is not in front of the trike. Physical light/horn/DRS observations,
switch operation and controlled shade/light tests must be completed later.
Remote post-deployment performance and service checks are recorded separately
below when complete. Electrical/software checks do not certify physical motion.

## Recorded deployment and remote checks

Code commit `c1e375f` was pushed to GitHub main before deployment. The scoped
deployment completed at approximately 10:08 AEDT. Backup:
`/opt/hpr/backups/gpio-reliability-20261011T230813Z`. Both installed module hashes
matched the committed source afterward. `/etc/hpr/hpr.yaml` was byte-identical
to its backup after the three service restarts and acknowledgement checks.

The first 30-second steady sample measured GPIO at 2.9% of one core, compared
with 59.9% before deployment. None of the old GPIO edge IRQ subscriptions
remained. ADC readings and five-second pin evidence were fresh. GPIO reported
no pin readback errors and had zero automatic restarts.

All six fresh command acknowledgements passed without changing physical output
states: headlight OFF then restored AUTO, auxiliary lights OFF, demister OFF,
horn OFF and duplicate DRS RIDER_CONTROL. Round-trip acknowledgement times were
150.5, 199.9, 194.5, 171.0, 199.2 and 214.4 ms respectively. The repeated DRS
request did not reassert PWM; GPIO12 remained an input after release. DRS now
reports mode RIDER_CONTROL, source LOCAL and position OPEN, matching the current
grounded/active switch interpretation and the central startup mode.

The GPIO tests also passed on the Pi: 18 tests using mock pins, with no real
actuation. Repository gateway validation passed GPIO, ADC/config, GPS service
and receiver presence, BLE role separation/services, pedals, TPMS, derailleur,
both camera workers, video progress and native HDMI services. It still flags
the existing `/dev/fb0` check even though native KMS validation passes. These
checks do not prove worn-rider sensor reception or the physical monitor image.

Unresolved feedback discrepancy: all three load feedback inputs currently
report ON while the headlight, auxiliary and demister output commands are OFF.
GPIO5 is low and GPIO24/25 are high, matching the central output polarities for
OFF. GPIO27 is low (configured active-low), while GPIO22/23 are high (configured
active-high), which explains their reported ON states. This requires a physical
load/voltage/polarity check; no configuration was inverted just to make status
look correct. Visual/audible component tests and physical switch changes remain
pending because the operator cannot currently observe the trike.

A subsequent uninterrupted two-minute idle test averaged 2.92% of one core;
the four 30-second windows measured 2.93%, 2.90%, 2.87% and 3.00%. All input,
output-state and mode topics had only their initial retained delivery, with no
ongoing state chatter. Exactly 24 GPIO evidence messages arrived at the intended
five-second cadence. The ADC remained online with fresh readings, the GPIO PID
remained unchanged and automatic restart count stayed zero. The three affected
services are enabled at boot. No GPIO edge IRQ subscriptions returned.

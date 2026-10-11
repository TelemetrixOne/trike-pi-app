# GPIO reliability review and live test plan

Date: 2026-10-12, Australia/Sydney. Authoritative source reviewed:
GitHub main `a86db12b4011cf6f583bde7f503c29bd14c69e1b`.

The Pi is offline. These findings describe repository behaviour and local
simulations, not a fresh comparison with `/opt/hpr`, systemd or
`/etc/hpr/hpr.yaml`. No production GPIO code, configuration, web service or
Home Assistant setting was changed. Do not use the historical wiring/polarity
notes in `centrally-managed-gpio.md` as today's configuration.

## What is already reasonably quiet

Physical switches and feedback inputs use gpiozero edge callbacks with a
50 ms debounce. They are not all polled by the Python control loop.
Headlight AUTO is evaluated at the configured poll interval (default 0.2 s).
Since `45ec3fa`, an unchanged AUTO result does not republish circuit states.
The offline audit confirmed zero state publications for 300 unchanged checks.

The existing controller sends GPIO readback evidence every five seconds.
The GPIO synchroniser reports configuration status every ten seconds by
default; identical profiles do not restart an already healthy service.
The service-health publisher checks GPIO evidence separately and currently
expects it within at least 15 seconds. Increasing evidence intervals without
adjusting that contract would create false failures.

ADC telemetry samples every second by default and can publish seven changed
values per sample. Those measurements are rounded but not rate-limited beyond
sampling. That is at most roughly seven telemetry messages/second at default
settings, not proof of a command flood. GPIO subscribes to command `/set`
topics, not its own state topics: no direct state-to-command feedback loop was
found inside the GPIO controller.

## Reproduced weaknesses

Run `python scripts/audit-gpio-offline.py`. It uses mock devices, synthetic
configurations and a disconnected MQTT client. It performs no GPIO, broker or
SSH operations. Temporary synthetic files are automatically removed.

| Finding | Offline evidence | Consequence and limit of evidence |
| --- | --- | --- |
| ADC conversion waits can spin without a deadline | Local ADS driver 3.0.5 makes over 600,000 fake conversion-status checks in about 50 ms; the wait has no timeout | GPIO calls ADC reads synchronously, before horn expiry and DRS release checks. A stuck conversion can delay those timers and consume CPU. Fake I2C is instantaneous; this is not a measured Pi bus rate or confirmation of the Pi's driver version. |
| Unchanged AUTO states still write outputs | 300 unchanged checks invoke `output.on()` 300 times, with zero state publications | Redundant output writes are unnecessary. Five calls/second by itself does not explain yesterday's reported slowdown. |
| DRS commands block their caller and repeat already completed motion | Two CLOSE commands to an already closed mock servo with a 0.5 s transition request 98 sleeps totalling 0.98 s | `_set_drs` sleeps inside MQTT or switch callbacks while holding a DRS lock. The control loop also takes that lock for evidence/release. Transition settings can be up to five seconds. |
| Retained actuator commands are inconsistently rejected | Retained horn ON and DRS OPEN are accepted; only headlight-mode retained commands are rejected | Broker reconnect can replay old actuation and override central DRS startup mode. State and desired configuration should remain retained; transient actuator commands should not. |
| Disconnected state publishing can accumulate indefinitely | A disconnected Paho client queues all 1,000 QoS 1 publishes; default queue limit is zero, meaning unlimited | GPIO publishing does not check connection state or bound its queue. A broker outage can accumulate stale state for later delivery. This is a backlog risk, not proof that it happened yesterday. |
| Two configuration consumers own the same GPIO section | A valid GPIO-only profile B is replaced by valid full-trike profile A in the simulation | Both consumers replace GPIO and can restart its unit; no shared transaction lock/revision ordering exists. Both central builders use the same GPIO renderer, so this requires differing/stale envelopes or a concurrent write. Live retained payloads must be compared. |
| Polling/sampling timing is not validated | Zero poll, ADC sample and retry intervals pass GPIO validation | A zero interval can remove intended rate limits; negative values can fail at runtime. Valid finite bounds must be checked centrally and on the Pi. Current live values are unknown. |

Other source concerns requiring targeted tests:

- GPIO logic can run concurrently in MQTT callbacks, input callbacks and the
  main AUTO loop. Only DRS has a lock; a single control owner would make command
  and output ordering easier to reason about.
- Sensor read failure leaves previously sampled ambient values available to
  AUTO without an age check. A stale sensor reading is not fresh light evidence.
- Repeated sense-input callbacks publish regardless of whether their semantic
  state changed. Noisy or marginal feedback signals may generate extra work;
  actual edge rates and electrical levels need measurement.
- The binary-switch helpers use legacy polarity conversion, unlike the newer
  `electrical` sense-input path. Every enabled local switch must be checked
  against its actual pull and active level. `headlight_mode` currently uses
  software mode even if a binary physical switch is supplied in its profile.
- Legacy three-position AUTO accesses `runtime.auto_latched_on`, which is not
  initialised in `CircuitRuntime`. A first reading inside the hysteresis band
  can fail. Whether this branch is enabled on Trike1 is unverified.
- ADC/I2C resources are not explicitly closed before reinitialisation. Repeated
  hardware errors need resource and retry testing.
- The demo listener uses existing commands and is idle unless explicitly
  requested. It is not evidence of an automatic startup show or an idle loop
  constantly changing lights. Its command restoration can overwrite an
  operator change made during the sequence; that override was explicitly
  requested previously and must remain an intentional contract.
- Historical inventory records a separate `/opt/hpr/trike1-health-agent` capable
  of restarting GPIO. Its current policy and activity require live inspection;
  repository health status alone does not establish who restarted a service.

## Proposed simpler implementation

1. One owner processes GPIO commands, debounced physical events and timers.
   MQTT and input callbacks enqueue events and return promptly. Preserve the
   current topic/mode contracts and the centrally supplied assignments.
2. Write relay outputs only when the effective state changes. Publish changed
   state promptly, a complete snapshot on reconnect, and bounded health
   evidence on the existing cadence. Duplicate requests may acknowledge the
   current result without re-actuating a load.
3. Keep ADC reads independent of control deadlines. Bound conversion waits
   and retry intervals, wait between readiness checks, timestamp readings and
   expose sensor faults. Determine the AUTO fault policy from the agreed
   central contract rather than silently using old readings forever.
4. Make DRS transitions advance on timers, without callback sleeps. Do not
   restart a completed identical movement. Keep central pulse calibration and
   hold/release behaviour; verify the actual Pi PWM backend before changing it.
5. Reject retained transient actuation consistently. Keep retained states and
   centrally desired configuration. Bound disconnected publishing and send
   current snapshots after reconnect instead of replaying a long history.
6. Give GPIO one central reconciliation path. If both existing topics must be
   supported, route them through a common locked/revision-aware applier. An
   unchanged GPIO profile must not restart GPIO because another feature changed.
   Coordinate the central producer contract with the web-platform chat.
7. Validate finite positive timing, calibration and threshold values on both
   sides. Do not hard-code Trike1 relay polarity or override central settings.

These are proposed changes, not deployed fixes. Preserve the deployed baseline
until read-only live comparison and measurements identify the active paths.

## Live baseline before changes

- Compare GitHub main with live GPIO controller, configuration appliers,
  imported ADC/gpiozero/Paho versions and relevant systemd units/drop-ins.
  Inspect `/opt/hpr`, active commands, timers, cron and the legacy health agent.
  Preserve unidentified enhancements and root-only configuration backups.
- Read the current central Race & Trike Configuration and both desired
  envelopes through the supported sync architecture. Compare actual GPIO
  content as well as hashes; check arrival order and service restart logs.
- Capture a quiet baseline for two minutes: per-process and per-thread CPU,
  GPIO input edge rates, MQTT topics/counts, command-to-state latency, ADC read
  duration and DRS duty changes. Distinguish changing measurements from commands.
  Do not display credentials or write full config into diagnostic reports.
- Check initial mode, requested output, effective output and feedback
  separately. Feedback proves electrical sensing; pin readback alone does not
  prove the lamp or actuator moved.
- Record background HRM, TPMS, pedals, derailleur, GPS, cameras, HDMI and
  service health to detect regression. Keep the two BLE roles intact.

## Component test order and acceptance

The table uses configured function names, not assumed BCM numbers or polarity.
Resolve each pin, pull and active level from the fresh central/live comparison.
Capture initial settings; restore them at the end of each physical test. Start
with the trike stationary. No demo or arbitrary all-output cycling is part of
the baseline. No reboot is included.

| Test | Stimulus | Required result |
| --- | --- | --- |
| Idle | No commands or switch changes for two minutes | No relay/servo movement, command flood or unexpected service restart; bounded sensor/health traffic and measured low CPU. |
| Headlight manual | One OFF, then one ON command | Correct mode, output voltage, physical lamp and feedback; one output transition per requested change. Repeat same command: acknowledge without another transition. |
| Headlight AUTO | Controlled shade/light through both thresholds | Measured sensor values are fresh; configured hysteresis/debounce applies; no chatter near thresholds. Return to initial mode. |
| Aux lights | One OFF/ON pair | Physical lights and configured feedback agree, with correct relay polarity and no unrelated output change. |
| Demister | One OFF/ON pair | Correct physical operation and feedback; no interaction with headlight/aux channels. |
| Horn remote | One fresh ON request | One bounded pulse; expiry remains timely while sensor/DRS work occurs. Reconnect cannot replay an old retained pulse. |
| Horn physical | Press/release its switch, if configured | Correct input semantics and physical response; local/remote precedence is explicit; no chatter after release. |
| DRS remote | OPEN/CLOSED, then an identical repeat | Correct calibrated movement and reported mode; release/hold matches config; identical repeats do not repeat motion. Other controls stay responsive. |
| DRS rider control | Select RIDER_CONTROL; operate switch both ways | Software mode remains RIDER_CONTROL while position follows the physical input. Switching back to remote follows the agreed precedence. |
| Feedback inputs | Observe each configured sense input with its output OFF/ON | Correct active-high/low interpretation and stable debouncing. Report actual electrical problems separately from commanded state. |
| ADC telemetry | Observe battery/current/light and introduce a controlled read failure | Plausible calibrated values; bounded sampling/retries; explicit stale/fault state; relay commands and horn expiry stay responsive. |
| Broker interruption | Brief controlled outage/reconnect | Local controls and timers continue; bounded queue; latest state snapshot restored; no unsolicited horn/DRS movement. |
| Configuration replay | Republish identical current profile; change one GPIO field through central config | Identical profile causes no restart. A genuine change applies once, preserves unrelated services, and reports the matching hash. |
| Combined load | Repeat GPIO actions with cameras, HDMI and BLE telemetry active | No lost rider telemetry, video freeze or large command delay; record CPU/thread/message rates against the baseline. |
| Demo | Run last, after all individual components pass | Explicit initiation only; correct blackout and sequence; original settings restored; no residual commands or continual activity afterward. |

Proposed performance targets, to be confirmed with measured hardware/network
latency: quiet GPIO process average below 5% of one CPU core over two minutes;
local event/control decision below 100 ms excluding deliberate DRS movement;
remote command-to-state acknowledgement below one second on a healthy broker
connection; remote horn pulse expiry within one control tick of its configured
deadline. These are targets, not results already achieved.

Once a source fix is reviewed, commit/push to GitHub main before using a scoped,
backup-producing repository deployment script. Restart only the affected GPIO
services. Validate each component and the unrelated telemetry/video services;
keep the exact backup and rollback command with the deployment report.

## Completed offline validation

- The diagnostic audit reproduced all seven findings listed above.
- `python -m unittest discover -s tests`: 66 tests passed. These cover existing
  behaviour, not all of the proposed reliability contracts.
- Pi comparison, actual CPU/message rates, current central settings and every
  physical component test: not run, because the Pi is offline.

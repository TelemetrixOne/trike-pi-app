# DRS command and status reliability

Only DRS handling is changed. Pins, calibration, PWM frequency, timings, startup
mode, switch debounce/polarity and all other GPIO configuration remain central
and unchanged. No Home Assistant or other service changes are required.

The old state is a commanded target, NOT a position sensor. DRS has no physical
position feedback. A `released` phase means only that PWM was released; it does
not prove the mechanism moved or stayed in position.

## Compatible protocol

Existing `TOPIC_ROOT/TRIKE/control/drs/set` accepts OPEN/CLOSED/RIDER_CONTROL plus
ON/OFF/CLOSE aliases. Retained commands remain ignored. Existing state/source/mode
reports are preserved (`state=CLOSE`, `mode=CLOSED` retain legacy spelling).
Legacy commands remain uncorrelated; a same-target legacy duplicate still does
not re-pulse. A deliberate retry uses the new identity-bearing interface.

New non-retained QoS1 `.../command` is JSON:

```json
{"schema_version":1,"command_id":"7bf20600-fdf3-4d55-b759-e88f66586bb8","mode":"OPEN","issued_at":1792750000.0,"expires_at":1792750005.0}
```

UUIDs identify intentional actions. TTL must be >0 and <=5s; clock readiness is
required, >2s future timestamps are rejected, and expired/retained/malformed
envelopes never actuate. Do not queue or automatically replay these commands.
The DRS clock check runs in its own bounded background thread, not the GPIO loop.

Identities/results are held for 60 monotonic seconds, bounded to 256. Live cache
entries are NOT evicted to admit new commands: capacity is rejected rather than
allowing an evicted QoS duplicate to re-actuate. Repeated identity -> duplicate,
with original_result and reason already_accepted/already_busy/etc. No duplicate
may restart movement or extend release time. A fresh identity can intentionally
re-pulse the same target after PWM release, with >=1s since previous actuation.
Same-target moving/finite-holding commands return busy; opposite-target reversal
remains available. Rejected/busy IDs need a fresh UUID for a later retry.

Non-retained QoS1 `.../ack` includes result accepted/rejected/duplicate/expired/busy,
command_id and reason where applicable. Accepted means Pi scheduling succeeded,
not actual position. Actuator-driver failures become rejected/error, rather than
crashing the shared GPIO owner or pretending success.

Retained QoS1 `.../status` publishes on meaningful change and every 5s. Both status
and ack include schema_version1, a service-instance boot_id UUID, increasing seq,
published_at epoch, mode, canonical target OPEN/CLOSED, source LOCAL/REMOTE,
phase idle/moving/holding/released/error, last_command_id and position_verified
false. They also include clock_ready and error details if applicable. Sequence
is shared across the two topics, so per-topic gaps are legitimate. Legacy mode
overrides clear last_command_id; local rider-switch changes preserve the accepted
RIDER_CONTROL identity. Do not infer fresh state from an old retained receipt:
compare original published_at and track subsequent heartbeat/boot/sequence.

## Deployment and tests

`scripts/update-pi-drs-reliability.sh` refuses unidentified old source or an
existing unknown helper, backs up source/config/units, installs only the helper
and GPIO-owner source, restarts ONLY hpr-gpio-control, then uses the passive
DRS-only validator. It never changes central config, subscribes to other control
topics for testing, or publishes live actuator commands. Automatic source rollback
also restarts that owner; config is never rolled back over newer central data.

The owner is shared with other outputs. Deploy only with stationary/restart
approval because startup reinitialises outputs and may move DRS to the configured
Rider Control position. No Pi reboot is needed or authorised.

Tests cover expiry, invalid input, clock rejection, replay/cache exhaustion,
correlated accepted/error acknowledgements, same-ID suppression, deliberate
fresh-ID retry, cooldown/busy handling, reversal, target vs position semantics,
bounded heartbeat, driver failure containment and retained-command rejection.
The live validator reads only DRS status: two fresh increasing heartbeats, stable
service identity and clock readiness. It sends NO actuation commands. Physical
switch, Pit Retry, movement under load and position retention after pulse-release
still require separately approved observation/tests.

Rollout note (23 October 2026): the operator authorised the shared-owner restart
at/after 18:35 AEDT. The earlier read-only audit matched the relevant live source
to GitHub main, but the deployment preflight found SSH unreachable and Tailscale
reporting the Pi offline. Source can be published while offline; live alignment,
deployment and passive validation remain pending a fresh online comparison.

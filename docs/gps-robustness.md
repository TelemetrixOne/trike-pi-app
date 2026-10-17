# GPS robustness — October 2026

The NEO-M9N recovered a 3D fix after an operator reboot, but the original loss
of fix is not conclusively explained. Its `txbuf alloc` warnings occurred both
without a fix and with a valid 15-satellite fix. Do not assume they establish
the cause or change receiver flash settings during racing.

Confirmed improvements:

- The receiver-specific udev rule replaces Debian's earlier `gpsdctl@` request
  rather than appending a second startup owner. The trike hotplug handler
  remains responsible for restarting only gpsd and the GPS publisher.
  ModemManager already reports `ID_MM_DEVICE_IGNORE=1` for this device.
- Receiver traffic and position validity are separate. The publisher emits a
  retained status heartbeat every five seconds, with fix mode, fix validity,
  fix age, receiver age and observation timestamp. Navigation output requires
  an explicit 2D/3D mode, finite in-range coordinates and a timestamp no older
  than five seconds (the configured stale timeout).
- Health requires fresh status and a real fix. No-fix traffic is Degraded;
  missing/stale status is Failed when race monitoring is enabled. Existing
  Disabled/legacy card behaviour is preserved outside race monitoring.
- Silent sessions reconnect with bounded exponential backoff. SKY-only traffic
  cannot hide missing TPV reports indefinitely. Broker startup uses Paho's
  asynchronous retry and a bounded outbound queue, rather than exiting.
- Prolonged no-fix is logged at most once per minute. Loss of satellite
  reception does not trigger receiver resets, USB resets or Pi reboots.

Deploy the committed repository with:

```sh
sudo bash scripts/update-pi-gps-robustness.sh --require-fix
```

This targeted updater refuses unidentified live Python enhancements, preserves
all current configuration, backs up affected files under
`/opt/hpr/backups/gps-robustness-*`, and restores previous files if validation
fails. It restarts only gpsd, hpr-gps and hpr-services-health. It does not run the
full installer, change camera quality, touch GPIO/BLE, or program the receiver.
The full installer also writes the corrected single-owner udev rule.

The repository validator observes live MQTT GPS status and health heartbeats;
`--require-fix` additionally requires a valid position. USB unplug/replug,
cold boot and extended track testing remain hardware checks. They must be
scheduled, not induced mid-race. Future no-fix investigation should capture
raw NAV-PVT/NAV-SAT and MON-RF before any recovery, plus gpsd and kernel logs,
to distinguish RF/antenna loss from receiver firmware or serial ownership.

## Fast fix-loss recovery (subsequent uplift)

`hpr-gps-recovery.service` independently watches local gpsd TPVs, not MQTT, so
network/broker outages never trigger receiver recovery. Ten seconds after the
last actual valid fix timestamp it captures diagnostic evidence to the journal
and requests restart of **only gpsd.service and hpr-gps.service**. The observer
stays running while they restart. After twenty seconds without reacquisition it
reports `failed` on the configuration-derived `nav/recovery` topic, which the
existing GPS health card reflects as Failed while race monitoring is enabled.
Fresh valid GPS always takes precedence over an older recovery failure.

There is a minimum sixty-second interval between attempts, including after a
watchdog process restart (timestamp persisted in its systemd StateDirectory).
Cold boot gets an acquisition grace period until roughly sixty seconds after
boot; restarting the watchdog later grants only ten seconds. Repeated or stale
TPVs, SKY reports and network traffic cannot advance the last-fix clock.
No receiver programming, USB reset, shared service restart or Pi reboot is
permitted by this watchdog. Service restart cannot guarantee reacquisition.

Deploy from the committed repository using
`sudo bash scripts/update-pi-gps-fix-recovery.sh`. The guarded updater preserves
central configuration and receiver settings, backs up affected state and rolls
back on failed validation. It starts the watchdog and restarts only service
health; it does not deliberately restart healthy GPS. Future fault-triggered
restarts are exercised with deterministic mocks, not by breaking live race GPS.
Cold boot and a controlled actual fix-loss recovery remain pit/bench tests.
`gps.recovery_enabled` defaults to true and can be supplied through supported
central configuration; endpoints/topics and identity remain configuration-derived.

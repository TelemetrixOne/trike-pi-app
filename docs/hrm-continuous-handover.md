# Continuous HRM rider selection

The central trike profile synchronises the enabled HRM MAC/rider assignments
into `heart_rate.monitors`. The Pi never creates its own rider mapping. The USB
Bluetooth controller assigned to the `heart_rate` role continuously scans only
these MACs, including while the selected HRM is connected by GATT on the
separate onboard `telemetry` controller. TPMS, pedals and derailleur retain
their existing adapter and connection-locking behaviour.

At startup the service collects a scan window and attempts the strongest fresh
eligible HRM. A selected HRM must produce valid BPM before being considered
active. During that connection, the service reads the onboard LE connection's
RSSI and compares it with fresh USB-scanned rivals. A handover requires a rival
at least 8 dB stronger for 6 seconds, at least 10 seconds of active service,
and a 20-second handover cooldown. These are technical defaults, not rider
assignments. The service can read optional `heart_rate.handover_margin_db`,
`handover_confirm_seconds`, `handover_min_active_seconds`,
`handover_cooldown_seconds`, and `rssi_poll_seconds` from its config, but the
current central sync supplies only monitor assignments, so the defaults apply
unless that architecture is extended. No handover is attempted
when the active link RSSI cannot be read; heartbeat loss still releases it.
RSSI values from two different radios can have a calibration offset, so the
margin requires physical rider-change testing.

Deploy with `scripts/deploy-pi-hrm-continuous-handover.sh` from GitHub main.
It preserves exact prior live modules and restarts only the HRM and scanner
recovery services. The paired rollback script restores both modules. A USB
re-enumeration now refreshes HRM discovery with SIGUSR1, even if Linux keeps
the same hci number, because its scanner object would otherwise be stale.
The active onboard GATT connection survives this refresh. If the HRM service
has already failed, recovery starts that service instead.

The watchdog detects repeated kernel scan-command failures on the assigned
USB controller even if HCI reports UP and BlueZ reports Discovering. It polls
every 10 seconds and uses errors from the previous 30 seconds, excluding those
preceding the last recovery. Empty candidate lists alone do not trigger resets:
a connected strap can stop advertising and other riders may be out of range.
Recovery keeps retrying with a 30-second cooldown (up to 120 seconds after failed
resets), rather than abandoning scanning after three attempts.

Upgrade from the initial continuous handover deployment using
`scripts/deploy-pi-hrm-scanner-health.sh`. It preserves both modules and restarts
only HRM and its watchdog. Revert with
`scripts/rollback-pi-hrm-continuous-handover.sh` and the printed backup path.

The current ASUS USB-BT400 dongle has repeatedly failed at the HCI level.
Software can recover it temporarily but cannot guarantee continuous scanning
until that hardware/firmware problem is resolved. Validate with two powered,
assigned HRMs and a controlled rider swap before race use.

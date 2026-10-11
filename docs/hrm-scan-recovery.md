# Trike1 HRM scan adapter recovery

The HRM service scans on the controller assigned to the `heart_rate` Bluetooth
role and connects over GATT on the separate `telemetry` controller. Do not
collapse these roles to one adapter. The controller addresses come from synced
configuration, not this recovery service.

`hpr-bluetooth-recovery.service` runs as root because an isolated USB device
re-enumeration requires sysfs access. Every 10 seconds it checks whether the
configured scan controller is UP and checks recent kernel scan-command errors.
If it is DOWN, it first tries `hciconfig up`.
If that times out, it resolves the controller's own USB parent from sysfs and
briefly reauthorizes only that device. It does not reset the USB hub, GPS,
cameras, or the GATT/telemetry controller. If Linux renumbers the recovered
scan adapter, it signals `hpr-heart-rate.service` with SIGUSR1 to rebuild only
discovery and resolve the stable controller assignment again. The onboard HRM
connection remains active. This also clears stale discovery when Linux reuses
the same hci number. Recovery retries continue with a 30-second cooldown,
increasing to 120 seconds after failed resets; there is no hourly cutoff that
leaves the scanner unavailable for the rest of a training session.
Controller role resolution uses the basic HCI listing, avoiding verbose
queries that can interfere with this dongle while scanning.
The watchdog also observes the HRM service's MQTT scan status. Three failed
discovery cycles within two minutes (as opposed to merely seeing no rider)
trigger the scanner-only USB reset and discovery refresh. Repeated kernel scan
command failures also trigger recovery, even if UP/Discovering remain true.
An empty scan without a scanner error never triggers this.

Deploy with `scripts/deploy-pi-bluetooth-recovery.sh` from a GitHub-main
checkout on the Pi. The script preserves the previous validator and refuses
to overwrite an unexpected module or unit. Use the printed backup directory
with `scripts/rollback-pi-bluetooth-recovery.sh` to remove the watchdog.
Normal deployment does not restart the HRM service or other BLE services.
For the already deployed 18bc99c watchdog, use
`scripts/update-pi-bluetooth-recovery.sh`; its paired rollback script restores
both changed Python modules and restarts only the watchdog.

For the continuous handover service, deploy the paired upgrade with
`scripts/deploy-pi-hrm-scanner-health.sh`; it backs up both modules and initially
restarts HRM and the watchdog. Subsequent USB recovery refreshes only scanning.

This protects against the observed USB controller hang. Continued hardware
errors still require inspection of the USB dongle, power supply and firmware.

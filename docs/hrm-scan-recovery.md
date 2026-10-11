# Trike1 HRM scan adapter recovery

The HRM service scans on the controller assigned to the `heart_rate` Bluetooth
role and connects over GATT on the separate `telemetry` controller. Do not
collapse these roles to one adapter. The controller addresses come from synced
configuration, not this recovery service.

`hpr-bluetooth-recovery.service` runs as root because an isolated USB device
re-enumeration requires sysfs access. Every 15 seconds it checks whether the
configured scan controller is UP. If it is DOWN, it first tries `hciconfig up`.
If that times out, it resolves the controller's own USB parent from sysfs and
briefly reauthorizes only that device. It does not reset the USB hub, GPS,
cameras, or the GATT/telemetry controller. If Linux renumbers the recovered
scan adapter, it restarts only `hpr-heart-rate.service` so the service resolves
its current name. Recovery attempts are separated by at least two minutes and
limited to three per hour; repeated failure is logged for hardware review.
Controller role resolution uses the basic HCI listing, avoiding verbose
queries that can interfere with this dongle while scanning.
The watchdog also observes the HRM service's MQTT scan status. Three failed
discovery cycles within two minutes (as opposed to merely seeing no rider)
trigger the scanner-only USB reset and an HRM-service restart to clear stale
BlueZ scan state. An empty scan without a scanner error never triggers this.

Deploy with `scripts/deploy-pi-bluetooth-recovery.sh` from a GitHub-main
checkout on the Pi. The script preserves the previous validator and refuses
to overwrite an unexpected module or unit. Use the printed backup directory
with `scripts/rollback-pi-bluetooth-recovery.sh` to remove the watchdog.
Normal deployment does not restart the HRM service or other BLE services.
For the already deployed 18bc99c watchdog, use
`scripts/update-pi-bluetooth-recovery.sh`; its paired rollback script restores
both changed Python modules and restarts only the watchdog.

This protects against the observed USB controller hang. It is not a substitute
for a sound dongle, power supply, or correct device firmware. If the watchdog
reaches its retry limit or hardware errors recur, inspect the USB dongle and
firmware rather than increasing reset frequency blindly.

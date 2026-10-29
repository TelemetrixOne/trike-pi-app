# Native MQTT-only gateway

GPIO control and ADC telemetry use the platform's native MQTT topics directly.
They no longer publish external entity-discovery configuration or require a
second automation endpoint. Native commands, state, DRS acknowledgements/status,
input feedback, ADC samples, service health and configuration ownership retain
their existing contracts. BLE scanning/connection roles, GPS and video are not
changed. No external automation service or unrelated infrastructure is stopped.

The old discovery publishers, metadata defaults, startup/reconnect hooks and
discovery options are removed, not merely disabled. The example configuration
and endpoint validation need only the native broker. Existing unrelated or
retired optional local YAML keys remain inert; this change does not rewrite the
live configuration or central profile and cannot trigger a configuration drift
reconciliation merely by removing obsolete keys.

## Deployment boundaries

Use a committed LF release archive and `scripts/deploy-pi-native-mqtt-only.sh`.
The updater requires separately confirmed stationary/shared-GPIO-restart
approval (`--stationary-approved`), because restarting the shared owner can
reinitialise physical outputs, including DRS. It refuses unknown live config/GPIO
source, backs up both files, config and the unit privately, installs only the
two source files, restarts only `hpr-gpio-control.service`, passively validates
DRS status and verifies the YAML is byte-identical. Failed validation restores
the previous source with one additional owner restart. It does not reboot,
publish actuator commands, alter BLE roles/camera tuning, or restart the
configuration writer or other hardware services.

Retained discovery entries already in a broker are not deleted by replacing
source. Coordinate exact-topic inventory, payload backup and ownership checks
with the central-platform owner after the old publishers have stopped. Never
clear a wildcard, native control/state topics, or entries belonging to unrelated
devices. Inert legacy local YAML keys and retained historical backups are not
proof of an active integration; remove configuration keys only through a
separately reviewed, coordinated configuration transaction. Source history is
not rewritten by this cleanup.

## Verification record — 30 October 2026

GitHub main baseline was `dbac49e`. The live Pi SSH preflight timed out, so no
live source/config/unit alignment could be verified and nothing was deployed or
restarted. Unfinished GPIO/DRS edits in the separate working checkout were left
untouched; this change was developed in an isolated checkout. Automated tests
cover native-only config validation, native state reconnect without actuation,
telemetry worker startup without discovery publication, and existing GPIO/DRS,
single-writer, BLE, GPS and video regressions. A live retained-topic inventory,
passive deployment validation and observed physical GPIO/DRS verification remain
pending; do not claim live removal while the Pi is unreachable.

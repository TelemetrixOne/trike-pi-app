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

## Separately installed observer migration

When the Pi returned, a separate `/opt/hpr/trike1-health-agent/health_agent.py`
was found actively publishing metadata, outside the canonical gateway modules.
The operator separately approved removing only that publisher and restarting
the health agent. `scripts/deploy-pi-health-agent-native-mqtt.py` refuses any
source other than the audited SHA256, privately preserves source/config/env/unit,
and uses `hpr_gateway.retired_metadata.strip_health_discovery` to remove exactly
the metadata method and its startup invocation. AST equality checks prove the
other source statements are unchanged. The original observer is not silently
replaced by a different monitoring implementation.

The updater reads native state and the retained enable setting before restart,
refuses unless natural subscription can preserve enablement, then requires a
fresh native state with the same enablement. It never publishes enable commands.
GPIO/video process fingerprints and gateway config must remain unchanged. This
step restarts only the health agent; it does not delete broker metadata. Any
failure leaves the private original backup available and reports for inspection.

## Live publisher removal — 30 October 2026

The Pi returned online. Fresh inspection matched the expected pre-cleanup config
and GPIO sources. With explicit stationary/shared-GPIO-restart approval, source
`bb1086a` was deployed via the guarded updater. Backup:
`/opt/hpr/backups/native-mqtt-only-20261029T231820Z`. DRS passive validation passed:
RIDER_CONTROL, target OPEN, phase released, position_verified false. GPIO owner
PID 6986 remained active with NRestarts=0; no actuator test commands were sent.

The separately approved health-agent migration source `8121a0f` was pushed before
deployment. Original SHA256 was
`313267400af1653f611786f10781b3ccfc9aae9a5e7707a2b1423898a4878750`;
the metadata-only result is
`87ec6a145243d1ab6df50b391839fee0f468112a845d1eee7e60d8ca6e5019fd`.
Backup: `/opt/hpr/backups/health-agent-native-mqtt-20261029T232037Z`.
Native monitoring/remediation stayed enabled, with a fresh native state after
restart. Health-agent PID 7751 was active, NRestarts=0. All remaining source
statements were structurally unchanged. No active Pi race-agent unit was found.

Both installed canonical sources matched committed bytes. Gateway YAML stayed
byte-identical, SHA256
`1627ffe5fc8b7f1eedc2b82a6469b1feac9cbf4ff37f274eee16f086e7185243`.
Configuration owner PID 1025, video owner 636 and MediaMTX 596 were not restarted.
No warning/error journal entries were observed for the two affected services
after deployment. All 178 tests passed. Physical GPIO/DRS confirmation remains
pending; software status is not a position sensor. Retained metadata cleanup is
coordinated separately with the web owner; inert endpoint removal needs a
separately approved config-sync reload. Private source/config backups and Git
history are intentionally retained, not silently scrubbed for public release.

The operator subsequently approved removal of the inert endpoint block and a
configuration-sync-only reload. `scripts/remove-retired-pi-network-endpoint.py`
requires that approval and the expected current YAML SHA256, refuses active
native endpoint keys or unknown block shapes, backs up config/unit privately,
stops only the full sync owner and takes the shared configuration lock. It
removes only the explicitly supplied block, validates the entire candidate and
uses the existing atomic writer preserving owner/mode. Two fresh applied v2
heartbeats must retain the same central profile hash; every other parsed YAML
value and the GPIO/video/health-agent process fingerprints must stay unchanged.
No remote control commands or hardware-service restarts are part of this step.

# Central configuration single-writer migration

The full `config/trike/desired` profile is the only automatic configuration input
after migration. This changes configuration ownership, not pins, DRS calibration,
camera tuning, Bluetooth roles, runtime switch modes or actuator command topics.
Central Race Configuration remains authoritative.

## Ownership and transaction safety

Full, GPIO, TPMS and pedal appliers share `.config-sync.lock` beside the YAML.
It spans read/modify/write, service application and interrupted-transaction
recovery. Before any new apply, an interrupted transaction from any applier is
recovered under the same lock. Multiple pending records fail closed for human
inspection. New full-profile transactions durably back up config and journal
previous affected service states; failed/interrupted changes restore config and
affected services. Native video rollback uses actual previous unit state, never
starts retired camera owners merely because video is enabled in the config.

Migration disables the three standalone config-sync units and installs unit
conditions against `/etc/hpr/.config-single-writer-v2`. Their apply/recover
entrypoints also reject once that marker exists; old retained scoped desired
profiles cannot be applied by manually starting or invoking those entrypoints.
The full owner never subscribes to scoped topics. Installer updates honour the
marker, so a normal later install cannot re-enable the retired writers.

The full owner always recalculates its effective candidate, including every
managed section. A matching stored hash alone is not sufficient for a no-op.
Identical candidates do not rewrite the YAML or restart anything. TPMS-only and
HRM-only changes affect only their own units; GPIO and video are not restarted
unless their actual centrally managed settings or enablement change.

## Capability and applied evidence

Retained QoS1 `config/trike/status` uses schema_version=2,
config_sync_version=2, owner=`trike-config-sync`, single_writer=true only with
the migration marker and verified inactive standalone units. A process-instance
UUID boot_id, increasing seq and original epoch published_at accompany a 5s
heartbeat. State is waiting/applied/error/drift. `hash` is the last successfully
processed full profile (null before one); `section_hashes` is populated only
while read-back matches the effective candidate. Its seven values hash the
canonical desired identity, heart_rate, derailleur, video, tpms, power_cadence
and gpio sections, not private local settings. No credentials/config contents
are included. Errors clear section evidence. Offline heartbeat queue is bounded.

Web must verify fresh increasing capability/status, persist combined-only
ownership once adopted, and never fall back to scoped publications just because
the Pi is offline or status stale. Broker receipt is not Pi applied evidence.
After verified migration and confirmation of the latest full desired publication,
the web owner may clear ONLY old retained `config/{gpio,tpms,power_cadence}/desired`
and `config/tpms_assignments`. No control topics are cleared or published.

## Guarded rollout

Use `scripts/update-pi-config-single-writer.py --expected-hash HASH` with root and
the installed gateway virtualenv from a committed LF release archive. HASH must
be freshly supplied by the central owner for this Pi/root. The updater verifies
known live source, refuses unknown helpers/drop-ins/pending transactions, reads
the retained full desired without publishing, checks that its hash matches, and
refuses if applying it would change ANY hardware service. It backs up source,
config, desired envelope and units privately under `/opt/hpr/backups/`, stops only
the config-sync units, rechecks config/source under the shared lock, installs
the migration and starts only the full config owner. The owner runs as root,
as service application already requires privileged systemd operations.

Two fresh applied v2 heartbeats and unchanged GPIO/video process fingerprints
are required. Effective config must be unchanged except full-profile hash
metadata. No GPIO/video/other hardware service restart or actuator test command
is part of this migration. A Pi reboot is neither needed nor authorised.

On any rollout error all config writers are stopped; the existing hardware
services continue. Source/config evidence is preserved. Do not automatically
re-enable legacy writers or restore old retained profiles: that could replay
stale settings. Diagnose with the private backup; a rollback requires checking
current central configuration and retaining fencing until one owner is ready.

Offline tests cover cross-process serialization, all-section drift, idempotent
no-op, TPMS/HRM isolation, incomplete profile rejection, scoped replay fencing,
foreign recovery, multiple-pending rejection, rollback and fresh truthful status.
Live deployment, boot persistence, and an observed configuration-save trial are
separate validations and must not be claimed while the Pi is offline.

## Trike1 deployment record — 24 October 2026

Source `61114a3` was pushed to GitHub main before rollout. Fresh live comparison
matched all four expected sync sources and showed exactly four canonical sync
processes. The central owner refreshed the authoritative hpr/trike1 profile hash
`421e81ab961814fd8f6edac09e70f43dda45bc6932d83fda904f3119a9d1715a`.
The passive retained desired matched, with zero hardware `changed_units`.

Guarded deployment backed up to
`/opt/hpr/backups/config-single-writer-20261024T060945Z` and restarted only the
full sync owner (17:09:50 AEDT, PID 3628, active, NRestarts=0). Three standalone
sync units became disabled/inactive with migration guards. All five installed
sources matched committed bytes. YAML remained byte-identical, SHA256
`19772601a85ce10186bf8dd53293c3e3fe13368c50c447deb9b4aaaa4ae0b311`.
GPIO PID 942, native-video owner 624 and MediaMTX 593 retained their original
start times and zero restart counts; legacy front/rear video units stayed inactive.

Both passive validator runs passed fresh increasing v2 applied evidence, stable
boot `abaf2c10-a260-4a4a-8b85-26fffd1c4e4d`, matching full hash and seven section
hashes. The when-reachable heartbeat was stopped after success. No runtime
hardware service restarted, no actuator test commands were sent and no Pi reboot
was performed. Web v1.17.0 adoption/scoped-retained cleanup is owned and validated
separately by the web chat. Boot persistence and an observed configuration-save
trial remain untested; no reboot or settings changes were made to test them.

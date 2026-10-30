# Pi assessment remediation — 30 October 2026

## Source changes

- Service health follows the applied full trike profile for TPMS selection and
  configured topic roots, reloads assignment changes, and marks disabled services
  explicitly. It no longer requires the obsolete scoped assignment topic.
- Video health reads native capture/publisher frame progress. A disconnected
  monitor does not fail independent streams. This does not measure end-to-end
  latency or prove visible picture quality.
- Log counts recognise emitted Python warning/error levels as well as journald
  priority. A bounded or failed journal read reports unavailable counts.
- GPS recovery leaves a receiver emitting fresh no-fix reports acquiring. After
  loss of a real fix it permits one quick recovery attempt; stalled transport
  retries use a persisted three-attempt/ten-minute budget and bounded backoff.
- A canonical, unprivileged health observer preserves native MQTT state fields
  but never restarts services. Systemd and dedicated recovery services own that.
  It replaces the separate legacy observer through a guarded migration.
- Demo requests require a UUID, schema version, issue time and expiry of at most
  five seconds. A durable private replay journal consumes accepted identities
  before queuing. The per-sequence sender does not reconnect and replay commands.
  The CLI uses the new envelope. No startup demo or image is introduced.
- Legacy heartbeat publication requires explicit opt-in. The web coordinator
  confirmed no current consumers of the old `hpv/01/gw/last_seen` topic.
- Installation now uses the actual repository root, rejects unsafe paths and
  inherited environments, and packages canonical observer/demo state directories.
  Rebuild requires explicit stationary approval and preserves config/drop-ins.
  No installer or rebuild has been run on the live Pi for this release.
- Exact dependency versions, an isolated candidate builder and pinned CI actions
  establish a reproducible test baseline. These are not a completed vulnerability
  audit, binary SBOM or proof of compatibility on new hardware.
- Apache-2.0 covers original code following the owner's delegated licence choice.
  Upstream MediaMTX MIT notice is preserved. See THIRD_PARTY_NOTICES.md for the
  outstanding transitive binary release gate.

## Deployment status and required sequence

The fresh comparison found 33 deployed gateway modules matching
`cf1bf7297b56c5b4cc9f7aa1a04809c715a7540d`; the migration-only AST helper was
correctly absent. After a temporary SSH outage, the guarded rollout from
`0e4db31` succeeded. Backup: `/opt/hpr/backups/assessment-health-20261030T000932Z`.
An earlier validation subprocess import-path failure caused a clean automatic
rollback; the path was corrected and pushed before the successful retry.

Restarted: `hpr-services-health`, `hpr-gps-recovery`, `hpr-demo-show`,
`hpr-heartbeat`, `hpr-trike-config-sync`. Retired/fenced
`hpr-trike1-health-agent`; started unprivileged `hpr-health-agent`, preserving
ON mode, thresholds and monitored unit list. GPIO/DRS, video/MediaMTX, GPS
publisher, HRM, TPMS, pedals and derailleur PIDs were unchanged. No actuator
commands, demo requests or reboot were issued.

All seven passive deployment checks passed: fresh accurate log telemetry,
current TPMS assignments, camera frame progress, canonical observer ownership,
demo listener readiness, fresh GPS observer state and full config hash.
The central profile hash remained
`ce1f3073f4db7a710ae3fef5780d09e54592ef14257472b15efa287b816b51dd`.
Raw YAML SHA after metadata migration:
`1a6a8581ce7cde0414450079f950404db734582c8932030ce7fd4420e3d8b542`.

Subsequent log analysis found the Bluetooth recovery monitor still held an old
config class in memory and rejected the removed legacy endpoint every ten
seconds. Repository helper `reload-pi-stale-config-reader.py` from `23c2c1e`
verified source/adapters, restarted only that monitor, and verified 22 seconds
without new stale-loader errors; all protected PIDs and both adapter states were
unchanged. Backup: `/opt/hpr/backups/stale-reader-reload-20261030T031336Z`.
The old errors remain in the honest rolling 30-minute count until expiry.
GPS subsequently reported fresh valid fixes; native camera workers all reported
fresh frames. TPMS selection is correct, but one selected sensor was offline.
HDMI was disconnected; no physical HDMI/device test was performed.

The following is the deployment/revalidation procedure; steps 1–5 were completed
for the observer rollout. GPIO credential reload and environment activation are
separate pending maintenance operations.

1. Fetch GitHub main and repeat the read-only comparison of source, active process
   commands, units/drop-ins, `/opt/hpr`, YAML and pending config transactions.
   Preserve every unidentified difference before proceeding.
2. Use `scripts/deploy-pi-assessment-health.py` with its expected-source hashes,
   protected backups, manifest, rollback and passive validation. Do not run the general
   installer/rebuild on the racing Pi as a shortcut.
3. Replace only service-health, GPS-recovery, heartbeat and demo runtime source,
   the shared MQTT helper and new modules/CLI. Install observer state directories,
   preserve enabled mode, and disable/fence the legacy observer only after its
   source/settings are archived. Coordinate with the single config owner under
   its shared lock; preserve applied profile/section hashes and hardware settings.
4. Restart only those affected observers/listeners and configuration sync if its
   metadata changed. Do not restart GPIO, video, GPS publisher, Bluetooth, TPMS,
   pedals or derailleur just to activate observer changes. Do not send demo or
   actuator commands as part of validation.
5. Passively check current native topic evidence, TPMS selection, frame progress,
   accurate log counts, no repeated resets for a fresh no-fix receiver, observer
   mode and absence of competing recovery ownership. Compare protected PIDs and
   settings before/after. Physical GPS-lock, HDMI and device tests remain separate.
6. Build an isolated candidate using `scripts/prepare-pi-environment.py` outside the
   active venv. It deliberately never activates an environment. Complete import,
   dependency and hardware compatibility checks before a coordinated maintenance
   window for activation; keep the old environment intact for rollback.

## Credential migration is separate and coordinated

The web-platform owner is coordinating revocation of exposed shared credentials.
Do not rotate the Pi independently. Existing MQTT clients retain their original
credentials on reconnect. This source adds a tested credential-only GPIO
reconnect: SIGHUP wakes a network worker while controller/output objects remain
alive; it refuses changes to broker, identity or GPIO settings. Installing that
capability into the old running process first requires one coordinated stationary
GPIO restart. Future credential reloads use `systemctl kill --kill-whom=main
--signal=HUP hpr-gpio-control.service` after atomic config staging. Other clients
still require their own credential reload/restart. Central confirmed the full
profile does not carry deployment broker credentials. Use a private handover
file, temporary dual-valid accounts, per-trike ACLs and a verified key-first SSH
transition preserving Tailscale. No secret values belong in commits or reports.

Pending release work includes GPIO credential-reload activation, isolated-environment
activation, coordinated credential migration and complete transitive binary
licence/advisory review. This document does not mark those tasks complete.

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
  It must replace the separate legacy observer through a guarded migration.
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

The previous read-only comparison found all deployed gateway modules matching
`cf1bf7297b56c5b4cc9f7aa1a04809c715a7540d`, with an identified separate legacy
health agent and inherited Python environment. A later fresh SSH attempt timed
out. **This remediation is source-only until a fresh live comparison and guarded
deployment succeed.** No claim of live alignment follows from the test results.

1. Fetch GitHub main and repeat the read-only comparison of source, active process
   commands, units/drop-ins, `/opt/hpr`, YAML and pending config transactions.
   Preserve every unidentified difference before proceeding.
2. Prepare a repository deployment script with expected-source hashes, protected
   backups, a manifest, rollback and passive validation. Do not run the general
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

Pending release work includes guarded live rollout, isolated-environment
activation, coordinated credential migration and complete transitive binary
licence/advisory review. This document does not mark those tasks complete.

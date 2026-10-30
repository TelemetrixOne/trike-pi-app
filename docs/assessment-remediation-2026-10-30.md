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
for the observer rollout. Dependency activation was subsequently completed in the
explicitly approved stationary maintenance window, as recorded below.

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

## Isolated runtime activated

The first candidate found a missing linker development library for PyPI lgpio.
No OS packages or active environment were altered. The corrected builder links
only OS-owned lgpio, its distribution metadata, `_lgpio` and gps before resolving
the pinned Python dependencies; it records native package versions and hashes.
No other system site packages are inherited. Failed/stopped candidates were
preserved for diagnosis.

Candidate `/opt/hpr/candidates/runtime-2ee99a1` passed `pip check`, exact runtime
version checks, import checks and all 195 tests on the Pi. Tests ran with private
devices/network, read-only system/kernel settings and GPIO mock factory; they
could not access the trike's hardware. Initial test packaging omitted the vendor
checksum fixture; adding a read-only link to the unchanged binary resolved that
fixture error, after which the complete run passed.

After direct human approval that the trike was stationary, repository activation
script from `fab83f4` moved the original environment into
`/opt/hpr/backups/isolated-runtime-20261030T033539Z/venv` and switched
`/opt/hpr/gateway/venv` to the validated candidate. All 15 active gateway Python
workers were restarted; GPIO restarted once. GPIO source plus the credential
reload helper were installed. No actuator commands or credential changes were
sent. Native video and MediaMTX PIDs remained 636 and 596; no video restart or
camera setting change occurred. All gateway Python workers now use the isolated
environment. The native video path continues to use system Python intentionally.

Restarted Python workers: `hpr-bluetooth-recovery`, `hpr-demo-show`,
`hpr-derailleur`, `hpr-gpio-control`, `hpr-gps-recovery`, `hpr-gps`,
`hpr-health-agent`, `hpr-heart-rate`, `hpr-heartbeat`, `hpr-pi-power-health`,
`hpr-power-cadence`, `hpr-power-watch`, `hpr-services-health`, `hpr-tpms`,
`hpr-trike-config-sync` (all `.service` units).

Passive before/after reported states matched: headlight AUTO/ON, auxiliary lights
OFF, horn OFF, DRS RIDER_CONTROL/OPEN. The GPIO process has its SIGHUP handler
installed; no signal/credential change was needed to establish that. GPIO pin
readbacks were Good (four outputs/four inputs), GPS had a fresh 3D fix/Good,
both Bluetooth adapters were UP/RUNNING with zero controller errors, all camera
workers had fresh frames and all seven central section hashes remained applied.
YAML and local camera files were unchanged. No HPR runtime warning/error entries
were found after activation during the validation interval; the rolling system
count still includes earlier diagnostics/test output until it ages out.

Physical actuator motion, rider handover and connected HDMI appearance/latency
were not tested by this maintenance operation.

## Advisory and attribution gates

The reproducible advisory query covered 112 declared Python/Go components.
OSV returned no matches for the pinned Python versions at query time, but
returned 38 advisory IDs (including aliases) for six declared Go components:
go-git/v5, webtransport-go, x/crypto, x/net, x/text and Go standard library.
The query is a version match, not proof of runtime exploitability or clearance.
OS/native media packages and dependency reachability require separate review.
That inventory described the original binary. See the patched replacement and
deployed boundary below, and `scripts/audit-pinned-components.py` for the original
query. A version match alone does not establish runtime exploitability.

A separately downloaded official MediaMTX v1.21.3 ARM64 candidate was verified
against GitHub's release-asset SHA256 and inspected without execution or Pi
deployment. Its Go 1.27.3 inventory still matched three Go advisory IDs across
x/crypto and x/net, including advisories published after that release. Therefore
an official binary upgrade alone is not security clearance. A patched broker
build, reachability review and isolated compatibility test are needed before a
separate video-broker rollout; the native camera/HDMI path need not change.

The patched build replaces that initial inventory. Its 65 compiled modules have
79 collected notice files, with package-scoped attribution for the linked
`github.com/benburkert/openpgp/aes/keywrap` package. The embedded hls.js 1.7.3
bundle has Apache-2.0 terms plus its Babel/regenerator MIT notices. Source,
provenance, notice hashes and ARM64/AMD64 reachability reports are committed.
Both architecture-specific govulncheck runs passed. A module-only x/crypto
advisory remains in the dependency graph without vulnerable package/symbol
reachability; this is not a blanket security or public-release clearance.
Repository visibility and history were not changed.

## Patched media deployment and shared-memory recovery

Source commits `ba72fda` and `a0bc507` vendor and validate MediaMTX
`v1.21.3-tm1.1`, built with Go 1.27.3 and patched x/net v0.62.0. The Pi ARM64
binary SHA256 is
`ee2c5ab2f61c3fd70173f8a959fe603a7bdff2b3451a379aed44a5a8ca298709`.
The independent broker updater is disabled; updates remain Git-managed.

Before deployment, isolated software-only old/new relay tests passed front/rear
with 120/120 frames, preserved RTP timestamps, denied untrusted read/publish,
and allowed trusted read while denying publish. These loopback timings are not
camera-to-display latency measurements. The local suite passed 198 tests and
84 subtests after the final changes.

The first live broker attempt rolled back when publisher children could no
longer open capture buffers. Capture processes still mapped deleted
`/dev/shm/shmpipe.*` objects while the host names were absent. The exact deletion
source was not conclusively traced; login IPC cleanup was a plausible cause.
Native supervision correctly restarted publishers, which then failed to reopen
the missing names. This caused a temporary stream outage even after restoring
the old broker.

Commit `84c3b7d` isolates `/dev/shm` in the native video service's mount namespace
and uses a writable runtime cache. One approved compositor restart regenerated
buffers; backup is `/opt/hpr/backups/video-private-shm-20261030T043350Z`.
The parent, both captures and both publishers shared the same mount namespace;
buffer names and streams survived SSH disconnect/reconnect. Real camera and DRM
device access remain available. No GPIO restart or actuator command occurred.

The repeated guarded broker rollout succeeded, with backup
`/opt/hpr/backups/media-broker-security-20261030T043428Z`. Only MediaMTX restarted
in that rollout. Native parent 103127, capture processes 103135/103136, GPIO
76885 and the other protected service processes remained unchanged.

The broker now serves RTSP/TCP only, exact front/rear paths, localhost
read/publish and deployment-enrolled remote readers with read-only permission.
Both verified remote reader hosts reconnected. API, metrics, playback, HLS,
WebRTC, RTMP, SRT and MoQ are disabled on this Pi broker. The prior unexpected
8892 listener was MoQ, not a metrics endpoint; it is now absent. Central HLS and
WebRTC remain the web platform's responsibility. The compositor requires the
console service and only wants the broker, allowing future broker maintenance
without stopping native capture or HDMI.

Both streams passed ffprobe and decoded frames after deployment: front H264
800x450 at 30 fps, rear H264 640x360 at 25 fps. Local tuning files and full central
profile hash were unchanged; configuration-sync reported fresh increasing
single-writer applied heartbeats. Timestamp overlays remained enabled. HDMI was
disconnected, so physical appearance and latency still require hardware testing.

Final retained telemetry reported video Good with all four workers advancing,
GPIO Good with four output/four input pin readbacks, and GPS Good with a fresh
3D fix. A sample showed CPU 58.2%, RAM 35.7%, temperature 42.8 C and disk 6.9%.
The 30-minute log metric reported zero warnings/errors, and a direct post-rollout
HPR journal query also found none. Failed-unit listings contained only preserved
transient development/test attempts from earlier in this maintenance, not failed
production services. Their journals were retained for diagnosis.

## Credential migration is separate and coordinated

The web-platform owner is coordinating revocation of exposed shared credentials.
Do not rotate the Pi independently. Existing MQTT clients retain their original
credentials on reconnect. This source adds a tested credential-only GPIO
reconnect: SIGHUP wakes a network worker while controller/output objects remain
alive; it refuses changes to broker, identity or GPIO settings. Installing that
capability was activated with the approved one-time stationary GPIO restart.
Future credential reloads use `systemctl kill --kill-whom=main
--signal=HUP hpr-gpio-control.service` after atomic config staging. Other clients
still require their own credential reload/restart. Central confirmed the full
profile does not carry deployment broker credentials. Use a private handover
file, temporary dual-valid accounts, per-trike ACLs and a verified key-first SSH
transition preserving Tailscale. No secret values belong in commits or reports.

The human deferred phone re-pairing, so old/shared credentials remain valid and
no independent credential revocation was performed. The web coordinator owns
per-device account/ACL provisioning and the eventual coordinated cutover.

Pending release work includes coordinated credential migration and broader
platform/history/export review. The scoped Pi binary and media-boundary work
above does not mark the complete product cleared for public release.

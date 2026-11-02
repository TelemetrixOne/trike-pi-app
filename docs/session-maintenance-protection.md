# Pi operator maintenance protection

Status: source staged and tested offline. Not deployed to the Pi. No connection,
service restart or configuration write was made during the protected practice.

Offline verification: full local suite 212 passed, with three Linux-only tests
skipped on Windows; all 17 maintenance tests passed under local Linux root,
including the real flock and nested Bash cases. All 46 shell scripts passed
syntax checks and Python compileall passed. A read-only check of the nominated
central HTTPS endpoint initially returned HTTP 403 from the operator machine on
2 November. A repeat request with the `TelemetrixOne-Pi` User-Agent returned fresh
schema-1 idle/ended authority, so the guard now supplies that explicit client
header. Generic Python requests still received 403. The Pi was offline on
Tailscale and SSH timed out at the attempted read-only live comparison; no live
files were read or modified. Activation remains held until it reconnects.

The web-platform protection endpoint is the authority. A deployment owner must
enrol `network.maintenance.protection_url` in `/etc/hpr/hpr.yaml` with the exact
trusted HTTPS URL for this Pi. No site URL, credentials, trike ID or topic root
is hard-coded. The file must be root-owned and not group/world writable. Missing
enrolment refuses maintenance; this also applies to initial installation. Enrol
the trusted runtime configuration before attempting the installer.

The endpoint's schema-1 response must match local `hpr.trike_id` and
`hpr.topic_root`, have `enabled:true`, `uses_pi:true`, a valid timezone-bearing
`server_time_utc` within 15 seconds of the local clock, `blocked:false`, and an
explicit `ended` or `draft` phase. Active, armed, scheduled, disabled, unknown,
malformed, stale, unreachable, redirected and TLS-invalid responses refuse.
Scheduled plus `blocked:false` is not treated as idle. Normal certificate trust
is required; environment proxy settings and force/URL environment overrides are
not accepted. Keep the Pi clock synchronised.

Protection evidence is atomically persisted and fsynced in root-only
`/var/lib/hpr/maintenance/protection.json`. A protected response latches across
process exit and reboot. Only a newer fresh idle response clears it. Backwards
responses or a changed enrolled identity refuse; lost connectivity never clears
a latch. No automatic retry queue executes deferred administrator work at idle.
Exit 75 means admission was refused. Evidence-directory creation and latch
updates are guard bookkeeping, not permission to modify operational files.

## Supported operator paths

All 40 shell operator scripts matching `deploy-*`, `update-*`, `rollback-*`,
`install-*`, `rebuild-*`, `apply-*`, `restore-*` and `video-timestamp-mode.sh`
source the common guard immediately after shell options, before operational
work. They hold one local flock across the operation and nested scripts.
DEBUG boundary checks, inherited into functions/subshells, re-fetch before
service/package/Python/build/archive/copy/move/remove/install commands and
recognised file-redirection commands. This is not a shell sandbox or a proof
that arbitrary shell syntax cannot bypass a boundary check.

All 10 Python deploy/activation/preparation/reload/removal/migration/test-broker
entrypoints and the console demo CLI use `guarded_main`. They admit before
`main`, hold the same flock and recheck before launched external processes and
Python-audited filesystem mutation boundaries. The console demo rechecks again
immediately before its MQTT request, after any confirmation prompt.
Child processes inherit the lease so nested supported scripts do not deadlock.
The isolated candidate pip job additionally polls protection every two seconds
and terminates only its own process group if admission is lost. An already
running package installation or live transaction is not killed asynchronously;
its next guarded boundary refuses. Pending transaction/backup evidence must be
preserved and reviewed after protection clears.

The existing offline builders/auditors and passive validators remain separate.
Validators with optional hardware perturbation, direct Python function imports,
direct MQTT demo requests, arbitrary root shell/systemctl, historical installed
script copies and manually started jobs are outside this operator-entrypoint
coverage. Do not run those as maintenance during a protected session. Existing
service processes are not wrapped by this operator guard.

## Live operations and remaining boundaries

No live GPIO controls, sensors, camera capture, bounded device recovery or
configuration-sync service source is changed. The full managed profile schema
is unchanged. Central blocks configuration pushes while protected. The Pi's
existing config-sync recovery/apply path does not yet independently consult
this HTTP guard: interrupted recovery or an out-of-band desired-profile publish
is a remaining gap, not covered by this source update. A service-level deferral
design must precede any change there, including its boot recovery behaviour and
honest deferred status instead of a false applied acknowledgement.

The public read endpoint is not a distributed reservation. Local flock excludes
other cooperating local administrator scripts, but central arming can race an
already admitted Pi operation between checks. Complete cross-machine exclusion
requires a separately designed maintenance reservation/arm handshake. The
current code does not claim atomic exclusion, root enforcement, or cancellation
of every already-running job.

## Activation after an idle window is verified

Repeat the read-only GitHub-main/live comparison, preserve unknown live files,
then enrol the trusted URL and install the common module/helper alongside the
exact operator scripts. Selective deployment archives must include
`hpr_gateway/maintenance.py`, `scripts/pi-maintenance-check.py` and
`scripts/pi-maintenance-guard.sh`. A script-only activation needs no GPIO,
camera, sensor or configuration-sync restart. Do not use an unguarded older
installer as an admission workaround. No credentials are changed by this work.

Before claiming live protection, test blocked admission without operational
mutation and confirm fresh idle admission, a denied competing local lease,
nested scripts, durable latching and HTTPS failure on the Pi. Hardware actuation
is not required for these checks.

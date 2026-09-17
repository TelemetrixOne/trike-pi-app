# Pi Gateway Repo Audit

Updated: 2026-08-01

Scope: repository scan after adding the Pi gateway implementation.

## Runtime Pi Gateway Scan

Scanned paths:

- `pi-gateway/`
- `scripts/install-pi-gateway.sh`
- `scripts/validate-pi-gateway.sh`

Result:

- No legacy `mqtt_hpv` credentials, Pi login credentials, or display-name-based MQTT topics are present in runtime Pi gateway code or installer/validator scripts.
- Endpoint lookup in runtime code is config-driven through `network.mqtt.host`, `network.home_assistant.*`, and generated central service metadata such as `network.mediamtx.*`.
- The only runtime code hit for `host` is the config loader reading `network.mqtt.host`.
- MQTT topic and Tailscale hostname values support `{topic_root}` and `{trike_id}` templates. Operator values are edited in `hpr-standalone-docker/config/hpr-race.yaml` and rendered into the per-trike runtime file.
- The Pi install root is generic: `/opt/hpr/gateway` by default. No repo-managed Pi runtime path includes `/trike1` or another trike-specific directory.
- Bluetooth services select stable controller addresses, not Linux enumeration order. The telemetry and HRM roles must use different physical controllers.
- GPS and camera devices use persistent `/dev/*/by-id/` paths.
- The clean rebuild disables legacy `hpr-hrmpro`, `hpr-pedals`, and `hpr-tpms-all` units before enabling the generic gateway equivalents.

## Allowed Matches

These matches are intentional and not runtime hardcoding:

- `config/hpr.example.yaml` contains the editable HPR standard credentials and display metadata.
- `docs/live-source-pi-inventory.md` contains live source inventory evidence.
- `scripts/collect-current-pi-over-ssh.sh` contains grep patterns for audit collection only.

## Existing Non-Pi-Gateway Matches

The wider repository still contains historical/reference matches in:

- `source/` read-only reference exports.
- Existing Docker migration docs and reports under `hpr-standalone-docker/reports/`.
- Existing Docker/Home Assistant/Nginx runtime files where endpoints, URLs, or browser asset URLs are part of the separate standalone Docker migration surface.

Those files were not changed as part of this Pi gateway task because the worktree already had unrelated edits and the user asked this task to preserve current behavior.

## Remaining Hardware Validation

Static validation does not prove RF reception, GPS sky view, GPIO wiring, or
camera enumeration. The post-rebuild Pi validator must pass with all hardware
connected before the rollback directory is removed.

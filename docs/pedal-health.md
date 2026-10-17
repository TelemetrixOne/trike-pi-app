# Pedal connection health

The pedal publisher's connection status is non-retained and normally emitted
only on state transitions. A restarted/reconnected health publisher can miss
`connected` while still receiving live power and cadence. Previously this
produced `running:disconnected`, `data:fresh`, `overall:Good` simultaneously.

Health now treats a fresh finite power notification (including zero while
coasting) as independent evidence of a connected pedal. Each device reports
its effective state, originally reported state, evidence source and data/status
ages. A newer explicit disconnect/not-found/connecting status takes precedence
over the last power sample; later live power supersedes an older disconnect or
nonfatal battery warning. Missing status without live data is Unknown, not an
invented Disconnected. Samples older than twenty seconds cannot prove a link.
All configured devices need fresh power for healthy aggregate data; process and
race-enabled handling remain unchanged. No extra BLE polling or messages are
introduced by this change.

`sudo bash scripts/update-pi-pedal-health.sh` guards the prior source hash,
backs up source and config, replaces only services_health.py, restarts only the
health publisher, and validates fresh live pedal messages and corrected health.
It automatically restores the previous source if validation fails. It does not
restart pedals, alter central configuration or change BLE adapter assignments.

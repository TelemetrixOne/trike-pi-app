# Pi system metrics for Pit Operations

The existing `hpr-services-health.service` publishes one additional retained,
QoS 1 JSON topic, `<health.base_topic>/system_metrics`. With the centrally supplied
configuration on Trike1 this is `hpr/trike1/pi/system_metrics`. No broker,
credentials or trike identity are hard-coded, and existing health topics are unchanged.

```json
{
  "schema": "hpr.pi_system_metrics.v1",
  "trike_id": "trike1",
  "cpu_usage_pct": 30.1,
  "memory_used_pct": 35.9,
  "cpu_temperature_c": 28.7,
  "disk_used_pct": 5.7,
  "warnings_count": 0,
  "errors_count": 0,
  "logs_window_seconds": 1800,
  "logs_scope": "system_journal",
  "logs_refresh_seconds": 60,
  "logs_complete": true,
  "logs_observed_at": "2026-10-13T00:00:00+00:00",
  "sample_interval_seconds": 10,
  "observed_at": "2026-10-13T00:00:00+00:00",
  "errors": []
}
```

Values above are illustrative. CPU is aggregate executing utilization, 0–100% across
all cores, calculated from successive `/proc/stat` counters (not load average).
It excludes idle/iowait and does not double-count guest time. RAM is
`100 * (MemTotal - MemAvailable) / MemTotal`, allowing reclaimable caches. Temperature
comes from the Pi thermal sensor, in Celsius.
`disk_used_pct` is used bytes divided by total bytes on the root filesystem (`/`),
expressed as a percentage and refreshed with the other resource values.

Warnings are journal priority 4; errors include priorities 0–3. These are counts of
entries, not distinct faults, over a rolling 30-minute window in the **system** journal
(including kernel and system services, not just HPR services). Queries are cached
for 60 seconds, timeout after five seconds, and are capped at 20,001 small JSON
records. Above 20,000 entries, malformed/inaccessible journals and query failures
produce null counts and `logs_complete: false`, never a misleading healthy zero.

Sampling runs in a daemon worker, outside service checks and MQTT callbacks. A
single additional message is published approximately every ten seconds; collectors
do not actuate hardware, restart services, create discovery entities, or modify
configuration/Home Assistant. The separate existing live health agent is untouched.

Web integration: add five items in Pi Services Health, styled like existing items:
CPU %, RAM %, Temperature °C, Disk used %, Logs with separate warning/error counts labelled
“last 30 minutes”. Treat null as unavailable, not zero. Check `observed_at` for
staleness (e.g. more than 30 seconds old); for logs also require `logs_complete`
and recent `logs_observed_at` (e.g. within 120 seconds). CPU requires two samples,
so can be null for the first ~10 seconds after startup. Never infer CPU usage from
the separate agent's `load_1m` value or treat a retained message as proof of liveness.

Deployment/rollback: use `scripts/deploy-pi-system-metrics.sh`, which guards and
backs up the existing source and configuration, installs only the two Python files,
and restarts only `hpr-services-health.service`. The backup contains `rollback.sh`;
invoke it with its absolute backup directory. After rollback, retained metrics can
remain in the broker; the UI must reject stale timestamps. Validate with the
repository `scripts/validate-pi-system-metrics.py` and normal gateway validation.

# Battery estimate and primary-pack replacement

The trike uses DeWalt DCB184-XJ 18 V nominal, 5 Ah lithium-ion packs. The spare
temporarily feeds the entire shared supply during replacement. Operators press
Battery Swapped in the spare cell of Pit Operations' Electrical System table
after the new primary is connected and the spare is removed.

## Estimator

Central configuration selects `gpio.adc.battery_soc.enabled: true`. Omitted or
disabled configuration retains the existing estimator for older installations.
The enhanced estimator uses a median-filtered, exponentially smoothed voltage
and a piecewise voltage/SOC curve. The legacy 85/15 charge-counting blend and
26% remapping are bypassed when this profile is enabled. Consumed Ah remains an
informational counter since initialisation or the last completed pack reset.

The supplied `liion_5s_reference` curve is an initial generic lithium-ion
reference. It has not been measured against this exact DeWalt pack, its age,
temperature or the trike's load. Status identifies it as `reference_estimate`.
A centrally provided `voltage_soc_curve` identifies it as
`configured_curve_estimate`. Both require pack/load validation before percentage
accuracy can be quantified.

| Pack voltage | Estimated SOC |
| --- | --- |
| 15.0 V | 0% |
| 16.5 V | 5% |
| 17.5 V | 10% |
| 18.0 V | 20% |
| 18.5 V | 40% |
| 19.0 V | 60% |
| 19.5 V | 75% |
| 20.0 V | 85% |
| 20.5 V | 95% |
| 21.0 V | 100% |

Default settings require at least five readings spanning five seconds with a
maximum spread of 0.15 V. Plausible pack measurements range from 12.5 to 21.5 V.
Normal filtering uses a 10-second time constant and limits displayed estimate
changes to 0.5 percentage points per second. A confirmed pack reset can establish
a new percentage after stable readings. Voltage jumps alone never reset usage.
Stable loaded voltage is still a loaded-voltage estimate, with uncertainty due
to sag, relaxation and temperature.

Negative, nonfinite and out-of-range readings publish unavailable SOC. Stale
samples also make the estimate unavailable. A recovered ADC must supply stable
readings before SOC returns; its existing usage counter survives recovery.
Ambient-light sampling and the GPIO owner continue independently of pack reset
processing. Existing relay and DRS behaviour is preserved.

The profile fields are `enabled`, `profile`, `voltage_soc_curve`,
`stable_seconds`, `stable_spread_v`, `valid_min_voltage`, `valid_max_voltage`
and `filter_seconds`. Configuration validation requires strictly increasing
curve points spanning 0 to 100%, valid numeric bounds and enough telemetry
samples to fill the stable window. The complete central GPIO profile owns these
settings and arrives through the existing full-profile synchronisation.

## Command and status

The configured MQTT identity supplies the topic root and trike ID.

- Command `.../control/battery/command`, QoS 1, nonretained.
- JSON fields `schema_version:1`, `action:"BATTERY_SWAPPED"`, canonical UUID
  `request_id`, `issued_at` and `expires_at` in UTC epoch seconds.
- The issue/expiry window is at most five seconds. Retained, expired,
  unsynchronised, oversized and malformed requests are refused.
- Status `.../control/battery/status`, QoS 1, nonretained, every five seconds and
  on changes. It includes `schema_version`, `boot_id`, increasing `seq`,
  `published_at`, `clock_ready`, `state`, `reason`, `request_id`, `soc_pct`,
  `voltage_v`, `quality`, `ah_used_since_reset`, `profile`, `method` and
  `settle_timeout_seconds`.
- States include `ready`, `settling`, `completed`, `rejected`, `invalid` and
  `stale`. Unavailable SOC is JSON null. Request acceptance first yields
  settling; only stable measurements yield completed for the same UUID.
- Default settling timeout is 30 seconds. The reported timeout accommodates
  configured longer sample windows. The UI waits that timeout plus five seconds
  and marks lost/stale communication explicitly.
- A duplicate current request republishes its state without resetting samples.
  The bounded, durable request journal in
  `/var/lib/hpr-battery-soc/requests.sqlite3` rejects replay across service
  restarts. A timed-out reset needs a new operator request.

## Commissioning and deployment

The Pi was unreachable during implementation. `scripts/deploy-pi-battery-soc.sh` is the
repository rollout entrypoint, using idle admission, pinned baseline hashes,
private backups and explicit stationary restart approval. It installs the
capability and journal directory drop-in, then restarts only GPIO and the full
configuration-sync owner. Central activation follows verified source readiness.
The script preserves the live YAML; it does not enable the feature locally.

On 20 November, the live Pi matched the pinned baseline and accepted idle
maintenance. Code `1a7eedc` was installed with backup
`/opt/hpr/backups/battery-soc-20261119T205237Z`. GPIO and the full configuration
sync owner restarted; other HPR process fingerprints and the live YAML were
preserved. All 16 battery regression tests passed on the Pi in a transient unit
with private devices/network, read-only system files and mock GPIO. The state
directory was created with mode 0700 and admin ownership.

CGS web code `45913c2` was deployed, and its central Trike 1 profile was enabled
with the implicit reference curve. The Pi temporarily became unreachable on
SSH, RTSP and Tailscale. After the operator restored connectivity, supported
configuration sync applied profile
`2e39cce77dc94b99d24f643e5c4e42ca13579f32990813c64b304dc9c2d10ea5`.
Independent read-back confirmed that only `gpio.adc.battery_soc` and the profile
hash changed from the installation backup. Fresh nonretained status reported
clock synchronisation and a ready reference estimate.

The operator explicitly confirmed primary connected/spare removed and approved
one test. A single authenticated TM1 API request used UUID
`c0a90a5c-7b98-4281-b3e6-23710c8551e5`. TM1 observed matching completion at
19.538 V / 75.8%, followed by resumed consumption. Independent Pi subscription
verified the same completed UUID, fresh timestamp, QoS 1/nonretained delivery,
increasing sequence, and exactly one durable journal claim. GPIO remained
active without an automatic restart during the command. Its only planned
restarts were capability installation and central profile activation; the full
configuration-sync owner restarted once during installation.

Final GPIO and camera health were Good. Separate sensor health reported GPS
recovery unsuccessful and stale pedal/TPMS notifications after reconnection;
HRM showed restart activity and no recent rider reading. Those services were
outside this source change. HDMI was disconnected. Pack/load calibration and
physical power-path protection review remain outstanding. The active 8095
trial instance was not deployed during this operation.

Compare Pi voltage with a multimeter at full charge and at several discharge
points under representative trike loads. Establish the relationship to actual
remaining usable capacity, then adjust the central curve. Verify one physical
swap with the spare removed, correct reset completion, continued camera/BLE
operation and expected handling of unavailable measurements.

Directly paralleled packs at different charge levels can exchange current
outside the software's control. The button cannot establish electrical isolation
or prove that the spare has been removed. The power arrangement needs a suitable
hardware changeover/reverse-current protection review before further hot swaps.

References: [DeWalt pack specification](https://www.dewalt.com.au/en-au/product/dcb184-xj/18v-xr-5ah-battery),
[TI gauging algorithms](https://www.ti.com/lit/an/sluaar3/sluaar3.pdf),
[battery isolation](https://www.analog.com/en/resources/technical-articles/dual-monolithic-ideal-diodes-provide-single-chip-power-management-solution.html).

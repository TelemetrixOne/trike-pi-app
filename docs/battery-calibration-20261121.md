# Trike 1 provisional usable-charge calibration

This profile belongs to CGS Trike 1's tested battery/electrical setup. It is not
a new default for other trikes, schools or battery types. Apply
`config/calibrations/trike1-battery-20261121.json` to that trike's
`gpio.adc.battery_soc` using the revision-aware configuration API.

The 21 November discharge started at 07:15:49 AEDT (20.183 V, defined as 100%).
The last Pi heartbeat was 08:41:14 AEDT, about 85 minutes later. USB recovery
included a reboot and load changes; these are not equivalent to a constant-load
runtime test. The consumed-Ah counter reset was accounted for in the analysis.
Its approximately 1.62 Ah relative discharge index is not a verified physical
capacity; do not replace the nominal battery capacity with this value.

The curve uses rounded charge-normalised observations, with linear interpolation.
The 12.6 V zero point is a conservative provisional near-empty anchor, **not a
measured shutdown voltage**. The estimator rejected voltage below 12.5 V and
the collector encountered errors after 08:37, so the exact cutoff voltage is
unknown. Zero means exhausted usable operation for this setup, not chemical
zero charge. A different load, battery or temperature can change the estimate.

The Pi estimator filters voltage, prevents percentage rebound until an explicit
Battery Swapped request, and checkpoints SOC/consumed Ah across process restarts.
Fresh stable measurements are required before a restored estimate is displayed.
Missing or invalid measurements remain unavailable. Use the existing Battery
Swapped button after physically replacing the pack; it estimates the replacement
from stable voltage rather than pretending every replacement is fully charged.

Both web instances already support custom curves and the
`configured_curve_estimate` quality. Malvern has no enabled Pi and receives the
profile/documentation files only; its trikes and race setup are unchanged.

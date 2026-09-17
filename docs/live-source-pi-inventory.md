# Live Source Pi Inventory

Inventory captured: 2026-05-24 14:58 AEST

SSH target used: `admin@100.105.239.87`

Capture method: read-only SSH commands and sanitized local file snapshots. No live Pi files, services, packages, Bluetooth settings, GPS settings, GPIO settings, NetworkManager settings, or systemd units were modified.

Local evidence folder used during migration: `C:\Users\shane\Documents\live-pi-captures\live-source-pi-20260524-145802`

## Host

| Field | Value |
| --- | --- |
| Hostname | `Ashton` |
| SSH user observed | `admin` |
| Working directory | `/home/admin` |
| Kernel | `Linux Ashton 6.12.62+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.12.62-1+rpt1 (2025-12-18) aarch64` |
| OS | Debian GNU/Linux 13 `trixie`, `DEBIAN_VERSION_FULL=13.3` |

## Network and Tailscale

Interfaces observed:

- `wlan0`: `10.0.0.15/24`
- `tailscale0`: `100.105.239.87/32`
- `lo`: `127.0.0.1/8`
- `eth0`: present, down/no carrier

Tailscale status:

- Source Pi: `100.105.239.87`, Tailscale name `raspberry-pi`
- Home Assistant host observed: `100.109.71.98`, Tailscale name `homeassistant`, active
- Multiple phone/client nodes are present in tailnet status.

Runtime rule: these IPs are inventory evidence only. Runtime endpoints must come from `/etc/hpr/hpr.yaml`.

## Active HPR-Related Services

`systemctl list-units` and `ps aux` both show these services/processes active:

| Unit | Active | Enabled | Observed command |
| --- | --- | --- | --- |
| `hpr-gpio-control.service` | yes | yes | `/opt/hpr/control/venv/bin/python /opt/hpr/control/hpr_gpio_control.py` |
| `hpr-gps.service` | yes | yes | `/usr/bin/python3 /opt/hpr/hpr_gps.py` |
| `hpr-heartbeat.service` | yes | yes | `/usr/bin/python3 /home/admin/hpr/services/heartbeat.py` |
| `hpr-hrmpro.service` | yes | yes | `/opt/hpr/hrm/venv/bin/python -u /opt/hpr/hrm/hrm_multi_to_mqtt.py` |
| `hpr-pedals.service` | yes | yes | `/opt/hpr/venv/bin/python /opt/hpr/pedals/hpr_pedals_all.py` |
| `hpr-pi-power-health.service` | yes | yes | `bash /opt/hpr/trike1/pi_power/hpr_pi_power_health.sh` |
| `hpr-tpms-all.service` | yes | yes | `/opt/hpr/tpms/venv/bin/python /opt/hpr/tpms/hpr_tpms_all.py` |
| `hpr-trike1-services-health.service` | yes | yes | `/opt/hpr/health/venv/bin/python /opt/hpr/health/hpr_trike1_services_health.py` |

Supporting active services:

- `bluetooth.service`: active/enabled
- `gpsd.service`: active, unit file disabled
- `tailscaled.service`: active/enabled

## Enabled or Present Non-HPR Services

| Unit | State | Classification |
| --- | --- | --- |
| `go2rtc.service` | unit file disabled, not running | `UNKNOWN_REVIEW_REQUIRED`; likely retired camera experiment, but config exists |
| `gpsdctl@.service` | static | GPS support |

## Systemd Unit Contents

Captured units confirm current production services run mostly as user `admin`, from mixed locations under `/opt/hpr` and `/home/admin`.

Important unit findings:

- `hpr-tpms-all.service` hardcodes MQTT host/user/password in `Environment=...`.
- `hpr-hrmpro.service` hardcodes MQTT host/user/password in `Environment=...`.
- `hpr-heartbeat.service` runs from `/home/admin/hpr/services`, not `/opt/hpr`.
- `hpr-pi-power-health.service` runs a shell script from `/opt/hpr/trike1/pi_power`.
- Service names include `trike1` in `hpr-trike1-services-health.service`; future repo-managed units should use generic unit names and read `trike_id` from config.

## `/opt/hpr` Inventory

Top-level `/opt/hpr` contains active services plus backups, probes, venvs, and logs:

- Active:
  - `/opt/hpr/hpr_gps.py`
  - `/opt/hpr/tpms/hpr_tpms_all.py`
  - `/opt/hpr/pedals/hpr_pedals_all.py`
  - `/opt/hpr/hrm/hrm_multi_to_mqtt.py`
  - `/opt/hpr/control/hpr_gpio_control.py`
  - `/opt/hpr/control/hpr_gpio_config.py`
  - `/opt/hpr/health/hpr_trike1_services_health.py`
  - `/opt/hpr/trike1/pi_power/hpr_pi_power_health.sh`
- Stale or review candidates:
  - `/opt/hpr/tpms/hpr_tpms_all.old`
  - `/opt/hpr/tpms/hpr_tpms_all.py.bak.*`
  - `/opt/hpr/tpms/find_tpms.py`
  - `/opt/hpr/tpms_capture_one.py`
  - `/opt/hpr/tpms_probe_one.py`
  - `/opt/hpr/trike1/tpms/hpr_trike1_tpms_rear.py`
  - `/opt/hpr/hrm/backups/*`
  - `/opt/hpr/hrm/*_test.py`
  - `/opt/hpr/hrm/hrm_to_mqtt.py`
  - `/opt/hpr/control/*.bak`
  - `/opt/hpr/hpr_gps.py.bak.*`
  - `/opt/hpr/logs/gps_*.jsonl`

No files were removed from the live Pi.

## `/home/admin` HPR-Related Inventory

Relevant files:

- Active:
  - `/home/admin/hpr/services/heartbeat.py`
- Review candidates:
  - `/home/admin/hpr_derailleur_ble_dump.py`
  - `/home/admin/tpms_capture.txt`
  - `/home/admin/_archive_tpms/*`
  - `/home/admin/hpr/trike1/tpms/venv/*`
  - `/home/admin/hpr-venv/*`
  - `/home/admin/udo systemctl enable tailscaled...` malformed shell-history-like file

The active heartbeat being outside `/opt/hpr` is a deployment-standardisation issue.

## MQTT Topics Discovered

Current active topics include:

- GPS:
  - `hpr/trike1/nav`
  - `hpr/trike1/nav/status`
- TPMS:
  - `hpr/trike1/tpms/tpms1`
  - `hpr/trike1/tpms/tpms1/availability`
  - `hpr/trike1/tpms/tpms1/raw`
  - same pattern for `tpms2`, `tpms3`, `tpms4`
  - placeholder code references `tpms5`, `tpms6`, `tpms7`
- Pedals:
  - `hpr/trike1/pedals/assioma_left/power_w`
  - `hpr/trike1/pedals/assioma_left/cadence_rpm`
  - `hpr/trike1/pedals/assioma_left/battery_pct`
  - `hpr/trike1/pedals/assioma_left/status`
- Heart rate:
  - `hpr/trike1/hr/bpm`
  - `hpr/trike1/hr/json`
  - `hpr/trike1/hr/status`
  - `hpr/trike1/hr/selected_mac`
  - `hpr/trike1/hr/selected_label`
  - `hpr/trike1/hr/selected_hrm_number`
  - `hpr/trike1/hr/selected_rider`
  - `hpr/trike1/hr/selected_rssi`
  - `hpr/trike1/hr/candidates_json`
- GPIO/control:
  - `hpr/trike1/control/<circuit>/set`
  - `hpr/trike1/control/<circuit>/state`
  - `hpr/trike1/control/<circuit>/source`
  - `hpr/trike1/input/<input>/state`
  - `homeassistant/.../config` discovery topics
- Pi health:
  - `hpr/trike1/pi/power_health`
  - `hpr/trike1/pi/throttled_raw`
  - `hpr/trike1/health/services/pi_power`
  - `hpr/trike1/pi/tpms_health`
  - `hpr/trike1/pi/gps_health`
  - `hpr/trike1/pi/heart_rate_health`
  - `hpr/trike1/pi/power_cadence_health`
  - `hpr/trike1/pi/gpio_health`
- Legacy heartbeat:
  - `hpv/01/gw/last_seen`

Do not silently remove or rename `hpv/01/gw/last_seen`; classify it as a legacy migration candidate.

## Bluetooth State

- Controller: `88:A2:9E:83:B0:2F`
- Name/Alias: display-name based, inventory value only
- Powered: yes
- Discovering: yes
- Roles: central/peripheral
- `rfkill`: `hci0`, `hci1`, and `phy0` not blocked
- Bluetooth journal: no recent entries in captured tail

The Bluetooth controller name contains a human display name and should be moved to config or left as host-local setup, not embedded in repo runtime code.

## GPS/gpsd State

- `gpsd.service`: active/running
- `hpr-gps.service`: active/running
- gpsd process observed: `/usr/sbin/gpsd -n /dev/ttyACM0`
- Device listing at capture time showed `/dev/serial0 -> ttyS0` and `/dev/ttyUSB0`; `/dev/ttyACM0` was not listed by the read-only glob.

`/dev/ttyACM0` versus current device listing is `UNKNOWN_REVIEW_REQUIRED`.

## GPIO Files and Config

Active GPIO files:

- `/opt/hpr/control/hpr_gpio_control.py`
- `/opt/hpr/control/hpr_gpio_config.py`

Current config evidence:

- MQTT host/user/password are hardcoded in `hpr_gpio_config.py`.
- Enabled `trike1` circuits:
  - `headlight`: output GPIO 5, boot default `OFF`
  - `tail_light`: output GPIO 6, boot default `ON`
  - `demister`: output GPIO 19, boot default `OFF`
  - `horn`: output GPIO 13, boot default `OFF`
- Enabled sense inputs:
  - `headlamp`: GPIO 17
  - `taillamp`: GPIO 27
  - `spare_headlamp`: GPIO 22
  - `spare_taillamp`: GPIO 23
- Disabled aux circuits exist for future/review.

GPIO service has richer telemetry and Home Assistant discovery behavior. Any repo replacement must be validated on hardware before replacing the live service.

## Camera / go2rtc / WebRTC / ffmpeg

Evidence:

- `go2rtc.service` exists but is disabled and not running.
- `/opt/go2rtc/go2rtc` and `/opt/go2rtc/go2rtc.yaml` exist.
- Config defines one stream: `trike1_front_camera`, using ffmpeg device path under `/dev/v4l/by-path/...`, 640x480 at 15 fps.
- No active camera/go2rtc/ffmpeg/WebRTC process was observed.

Classification: `UNKNOWN_REVIEW_REQUIRED`, likely retired/stale, but leave in place until reviewed.

## Hardcoded IPs and Endpoints Found

Runtime hardcoded values found in active scripts/units:

- MQTT/Home Assistant broker host: `100.109.71.98`
- Source Pi Tailscale IP: `100.105.239.87` appears in network inventory only.
- WLAN lease: `10.0.0.15`
- Loopback: `127.0.0.1`
- go2rtc listen ports: `:1984`, `:8554`, `:8555`

Repo implementation must read runtime endpoints from `/etc/hpr/hpr.yaml`.

## Hardcoded Usernames and Passwords Found

Hardcoded runtime credential findings:

- Active service units and scripts use old MQTT username `mqtt_hpv`.
- Active scripts contain hardcoded MQTT password values; local capture redacts password values.
- The active heartbeat script contains a legacy hardcoded password and legacy `hpv/01/...` topic.

Migration rule:

- Runtime credentials must be read from `/etc/hpr/hpr.yaml`.
- Repo runtime code must not contain hardcoded `mqtt_hpv` or password constants.
- Example config may contain the HPR standard `hpr` / `hPr01` pair as the desired editable runtime config.

## Display Names Found Outside Config

Display-name evidence:

- Hostname: `Ashton`
- Bluetooth name/alias: display-name based
- Existing documentation/report references in the Docker side describe the source HA dashboard template.

Runtime rule:

- Display names must be metadata only in central config.
- Technical identifiers must remain `trike1` through `trike6`.

## Active Components

Evidence-backed active components:

- TPMS BLE scanner/publisher
- GPS telemetry publisher
- GPIO/control service
- Gateway heartbeat publisher
- HRM BLE publisher
- Pedals power/cadence BLE publisher
- Pi power health publisher
- Services health publisher
- Bluetooth stack
- gpsd
- Tailscale

## Stale or Retired Candidates

Do not delete without approval. Candidates based on no active process/unit evidence or backup naming:

- go2rtc/camera stack: `UNKNOWN_REVIEW_REQUIRED`
- TPMS one-off capture/probe scripts
- archived single-sensor TPMS scripts under `/home/admin/_archive_tpms`
- HRM test scripts and backup revisions
- GPS backup scripts
- GPIO backup scripts
- old logs under `/opt/hpr/logs`
- duplicate venvs under `/home/admin` and `/opt/hpr`

## Components Requiring Review

- Whether legacy heartbeat topic `hpv/01/gw/last_seen` is still consumed by Home Assistant.
- Whether TPMS current Home Assistant entities expect `tpms1`-style topics or physical names such as `rear`.
- Whether `tpms7` placeholder code is unused or still referenced somewhere operational.
- GPS device mismatch: gpsd process references `/dev/ttyACM0`, device listing showed `/dev/ttyUSB0`.
- go2rtc camera config status.
- GPIO telemetry/discovery feature parity before replacing live GPIO service.
- Whether systemd services should run as a dedicated `hpr` user in the future; live services currently run as `admin`.

## 2026-09-17 GPIO Config-Only Update

Read-only checks began at 08:16:36 AEST using `admin@100.105.239.87`.
The host reported `hpr-trike1`, Debian GNU/Linux 13.3 (trixie), aarch64,
kernel `6.12.62+rpt-rpi-v8`. Authentication succeeded; the SSH password was
not written into a file or deployment script.

The live GPIO allocation was still the old version. The user confirmed that
the new wiring was already connected. In particular, the old configuration
used BCM6 as a tail-light output with an ON boot default, conflicting with the
new switch-to-ground input. With separate explicit approvals, these commands ran:

```sh
sudo systemctl disable --now hpr-gpio-control.service
sudo systemctl disable --now hpr-trike1-health-agent.service
sudo systemctl stop hpr-gpio-control.service
```

The health agent was included because its enabled remediation policy can
restart GPIO regardless of its systemd enabled state. Both units were verified
`inactive`, `disabled`, with `MainPID=0` after the update.

The user then approved a config-only push. A streamed Python update backed up
and atomically replaced `/etc/hpr/hpr.yaml`, preserving its `0640 root:admin`
permissions. It changed only the GPIO allocation/metadata and
`services.gpio_control.enabled=false`. Existing ADC calibration, network,
credentials, and all other service settings were preserved and compared.
The live configuration loader accepted the candidate and installed file.

- Outputs: headlight BCM5, aux lights BCM24, demister BCM25, horn BCM17.
- Inputs: headlamp BCM27, aux lights BCM22, demister BCM23, DRS switch BCM6.
- DRS switch: pull-up enabled, active low; new electrical-state interpretation
  requires the not-yet-deployed updated GPIO runtime.
- I2C: bus 1, SDA BCM2, SCL BCM3; `/dev/i2c-1` exists.
- DRS servo: BCM12 reserved and disabled.
- Relay polarity and hardware review remain `UNKNOWN_REVIEW_REQUIRED`.
- Backup: `/var/backups/hpr/gpio-config-1789597589329071092/hpr.yaml`;
  directory `0700 root:root`, file `0600 root:root`.
- Previous config SHA256:
  `0d9175b328cfc50b7486316b563f262a0cf671b8cb567c0746322716e27641b0`.
- Installed config SHA256:
  `95e0f98fb1e825ade98cd0bcb3cd2b392c0568c8f9dac78ac879c7beef16fb0c`.

No runtime source, systemd unit contents, packages, broker retained messages,
or web-platform files changed. The new GPIO sync service is still absent.
Post-update systemd checks showed GPS, TPMS, heart rate, power/cadence,
derailleur, heartbeat, power health/watch, services health, and front/rear
video/MediaMTX services still active. These are service-state checks, not
end-to-end telemetry or hardware verification. No reboot or actuator test ran.

Rollback requires restoring the protected backup with its original ownership
and permissions while keeping GPIO and the health agent stopped and disabled.
Never restart the old allocation against the new wiring. Hardware review,
updated runtime deployment, and controlled activation remain outstanding.

## 2026-09-17 Final Pi Software Deployment

The user explicitly approved the targeted runtime deployment and starting only
the sync service. Verification completed at 08:34:37 AEST. This supersedes the
runtime-deployment status in the preceding config-only entry.

Installed the four Python files from committed release `a05c5c7`:
`config.py`, `gpio_profile.py`, `services/gpio_control.py`, and
`services/gpio_config_sync.py`, under `/opt/hpr/gateway/hpr_gateway`.
The GPIO systemd unit now uses `Type=notify` with a 30-second startup timeout.
The new root-owned sync unit loads the same `/etc/hpr/hpr.yaml`; its config
flag is enabled with a 10-second status interval. No other config changed.

The approved update backed up all pre-existing affected files, checked their
baseline hashes, wrote each replacement atomically, and verified all seven
installed file hashes. Its protected backup and hash/ownership manifest are at:

`/var/backups/hpr/gpio-runtime-1789597981212470269`

The installed config SHA256 is:
`fd1755568970c749e681a4d5c05128f6fe5e5b029dd99509a8b492bbec894e66`.

Commands executed after file installation:

```sh
systemd-analyze verify /etc/systemd/system/hpr-gpio-control.service /etc/systemd/system/hpr-gpio-config-sync.service
env PYTHONPATH=/opt/hpr/gateway /opt/hpr/gateway/venv/bin/python -B -m hpr_gateway.validate_config --config /etc/hpr/hpr.yaml
sudo systemctl daemon-reload
sudo systemctl enable --now hpr-gpio-config-sync.service
```

Validation results:

- Focused local Pi tests rerun: 20 passed.
- Live config validation and systemd unit verification: passed.
- MQTT authentication and QoS 1 subscription: passed; no retained desired
  GPIO profile was present before sync activation. The inspection published
  no messages and did not change broker configuration.
- Sync: `active/running`, `enabled`, zero restarts at verification.
- Fresh, non-retained status received on `hpr/trike1/config/gpio/status`:
  `state=waiting`, `trike_id=trike1`. This proves live reporting, not profile
  application or physical GPIO operation.
- GPIO control and the auto-restart health agent: inactive and disabled.
- All seven live file hashes and existing-file backup hashes: matched.
- Twelve other HPR telemetry/video services: still active.
- No package installs, actuator tests, broker profile pushes or reboot occurred.

Rollback: stop and disable `hpr-gpio-config-sync.service`, restore the files
marked `existed=true` in the protected manifest with their recorded ownership
and modes, remove only the newly introduced files marked `existed=false`, then
run `systemctl daemon-reload`. Keep GPIO and the health agent disabled. The
backed-up config already contains the new pin map with GPIO disabled, so this
software rollback does not restore the unsafe old allocation.

Remaining: deploy the web-platform release separately, verify relay polarity
and feedback voltage, then approve controlled hardware activation. DRS servo
motion remains unsupported pending calibration and implementation. Reboot
recovery and end-to-end central profile application have not yet been tested.

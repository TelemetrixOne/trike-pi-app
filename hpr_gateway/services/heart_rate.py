#!/usr/bin/env python3
"""
hrm_multi_to_mqtt.py

HPR Trike1 multi-HRM BLE -> MQTT publisher.

v17: sequential single-adapter race handover state machine.

This version deliberately removes the complicated live RSSI selector/connection
interrupt behaviour from earlier versions.

Race rule implemented:
1. Scan known HRMs and rank them by strongest fresh RSSI.
2. Stop scanning completely before starting a GATT connection.
3. Connect to the strongest eligible HRM.
4. Give it HPR_HRM_CANDIDATE_BPM_PROVE_SEC to produce valid non-zero BPM.
5. If it succeeds, publish its rider identity and keep it active.
6. If valid BPM is lost for HPR_HRM_HEARTBEAT_LOST_SEC, publish "-" and release it.
7. If a candidate fails, suppress it briefly and resume scanning.

BLE scanning and the active GATT connection never overlap. This permits one
adapter to be dedicated to HRM work while the second adapter remains available
for TPMS, pedals, derailleur, and other trike services.

Important limitation:
If a removed/unworn HRM continues to send plausible non-zero BPM, there is no
reliable BLE signal in the observed Cycplus H1 payload to prove it is unworn.
The script will treat plausible non-zero BPM as valid.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple

from bleak import BleakScanner, BleakClient
import paho.mqtt.client as mqtt

from hpr_gateway.bluetooth import acquire_connection_slot, release_connection_slot, resolve_bluetooth_role
from hpr_gateway.config import HprConfig, config_arg_parser, enabled_items, load_config


# =========================
# Configuration
# =========================

TRIKE = "trike1"
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USERNAME = ""
MQTT_PASSWORD = ""
TOPIC_ROOT = "hpr"
ACTIVE_ADAPTER = "hci1"
SCAN_ADAPTER = "hci1"

HRM_CHR = "00002a37-0000-1000-8000-00805f9b34fb"

TOPIC_BPM = TOPIC_JSON = TOPIC_STATUS = ""
TOPIC_SELECTED_MAC = TOPIC_SELECTED_LABEL = ""
TOPIC_SELECTED_HRM_NUMBER = TOPIC_SELECTED_RIDER = ""
TOPIC_SELECTED_RSSI = TOPIC_CANDIDATES_JSON = ""

# Fresh scan age for candidate ranking.
SELECTION_FRESH_SEC = 6.0

# Duration of each bounded scan while no HRM is active.
SCAN_CYCLE_SEC = 6.0

# Let BlueZ settle after stopping discovery before opening a GATT connection.
BLE_SETTLE_SEC = 0.75

# Wider debug window for candidates_json.
SCAN_ACTIVE_WINDOW_SEC = 10.0

# If current active HRM stops producing valid non-zero BPM for this long,
# release it and search for the next closest candidate.
HEARTBEAT_LOST_SEC = 20.0

# Give a newly selected candidate this long to prove it is worn by producing
# valid non-zero BPM.
CANDIDATE_BPM_PROVE_SEC = 30.0

# Suppress failed candidates briefly so the manager can try the next strongest.
SUPPRESS_NO_BPM_SEC = 20.0
SUPPRESS_FIND_FAIL_SEC = 5.0
SUPPRESS_CONNECTION_ERROR_SEC = 5.0

# BLE operation timeouts.
FIND_TIMEOUT_SEC = 10.0
CONNECT_TIMEOUT_SEC = 20.0

# BPM sanity limits.
BPM_MIN = 30
BPM_MAX = 240

# Publish throttling.
PUBLISH_MIN_INTERVAL = 0.5
ONLY_ON_CHANGE = True

NO_BPM_PAYLOAD = "-"
RECONNECT_DELAY_SEC = 2.0


@dataclass(frozen=True)
class KnownHrm:
    number: int
    mac: str
    device_name: str
    description: str

    @property
    def label(self) -> str:
        return f"HRM #{self.number} - {self.description}"


KNOWN_HRMS: List[KnownHrm] = []
LABEL_BY_MAC: Dict[str, str] = {}
NUMBER_BY_MAC: Dict[str, int] = {}
DEVICE_NAME_BY_MAC: Dict[str, str] = {}
RIDER_BY_MAC: Dict[str, str] = {}
KNOWN_MACS: set[str] = set()


def configure(config: HprConfig) -> None:
    global TRIKE, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, TOPIC_ROOT
    global ACTIVE_ADAPTER, SCAN_ADAPTER
    global TOPIC_BPM, TOPIC_JSON, TOPIC_STATUS, TOPIC_SELECTED_MAC
    global TOPIC_SELECTED_LABEL, TOPIC_SELECTED_HRM_NUMBER, TOPIC_SELECTED_RIDER
    global TOPIC_SELECTED_RSSI, TOPIC_CANDIDATES_JSON
    global SELECTION_FRESH_SEC, SCAN_CYCLE_SEC, BLE_SETTLE_SEC
    global SCAN_ACTIVE_WINDOW_SEC, HEARTBEAT_LOST_SEC, CANDIDATE_BPM_PROVE_SEC
    global SUPPRESS_NO_BPM_SEC, SUPPRESS_FIND_FAIL_SEC, SUPPRESS_CONNECTION_ERROR_SEC
    global FIND_TIMEOUT_SEC, CONNECT_TIMEOUT_SEC, BPM_MIN, BPM_MAX
    global PUBLISH_MIN_INTERVAL, ONLY_ON_CHANGE, NO_BPM_PAYLOAD, RECONNECT_DELAY_SEC
    global KNOWN_HRMS, LABEL_BY_MAC, NUMBER_BY_MAC, DEVICE_NAME_BY_MAC
    global RIDER_BY_MAC, KNOWN_MACS

    hr = config.get("heart_rate", {}) or {}
    TRIKE = config.trike_id
    MQTT_HOST, MQTT_PORT = config.mqtt.host, config.mqtt.port
    MQTT_USERNAME, MQTT_PASSWORD = config.mqtt.username, config.mqtt.password
    TOPIC_ROOT = config.topic_root
    # The USB controller continuously scans/ranks known HRMs. The onboard
    # telemetry controller owns the selected HRM GATT connection.
    ACTIVE_ADAPTER = resolve_bluetooth_role(
        config, "telemetry", legacy_adapter_path="heart_rate.active_adapter", default_adapter="hci0"
    )
    SCAN_ADAPTER = resolve_bluetooth_role(
        config, "heart_rate", legacy_adapter_path="heart_rate.scan_adapter", default_adapter="hci1"
    )

    base = f"{TOPIC_ROOT}/{TRIKE}/hr"
    TOPIC_BPM, TOPIC_JSON, TOPIC_STATUS = f"{base}/bpm", f"{base}/json", f"{base}/status"
    TOPIC_SELECTED_MAC, TOPIC_SELECTED_LABEL = f"{base}/selected_mac", f"{base}/selected_label"
    TOPIC_SELECTED_HRM_NUMBER = f"{base}/selected_hrm_number"
    TOPIC_SELECTED_RIDER, TOPIC_SELECTED_RSSI = f"{base}/selected_rider", f"{base}/selected_rssi"
    TOPIC_CANDIDATES_JSON = f"{base}/candidates_json"

    SELECTION_FRESH_SEC = float(hr.get("selection_fresh_seconds", 6.0))
    SCAN_CYCLE_SEC = max(1.0, float(hr.get("scan_cycle_seconds", 6.0)))
    BLE_SETTLE_SEC = max(0.1, float(hr.get("ble_settle_seconds", 0.75)))
    SCAN_ACTIVE_WINDOW_SEC = float(hr.get("scan_active_window_seconds", 10.0))
    HEARTBEAT_LOST_SEC = float(hr.get("heartbeat_lost_seconds", 20.0))
    CANDIDATE_BPM_PROVE_SEC = float(hr.get("candidate_bpm_prove_seconds", 30.0))
    SUPPRESS_NO_BPM_SEC = float(hr.get("suppress_no_bpm_seconds", 20.0))
    SUPPRESS_FIND_FAIL_SEC = float(hr.get("suppress_find_fail_seconds", 5.0))
    SUPPRESS_CONNECTION_ERROR_SEC = float(hr.get("suppress_connection_error_seconds", 5.0))
    FIND_TIMEOUT_SEC = float(hr.get("find_timeout_seconds", 10.0))
    CONNECT_TIMEOUT_SEC = float(hr.get("connect_timeout_seconds", 20.0))
    BPM_MIN, BPM_MAX = int(hr.get("bpm_min", 30)), int(hr.get("bpm_max", 240))
    PUBLISH_MIN_INTERVAL = float(hr.get("publish_min_interval_seconds", 0.5))
    ONLY_ON_CHANGE = bool(hr.get("only_on_change", True))
    NO_BPM_PAYLOAD = str(hr.get("no_bpm_payload", "-"))
    RECONNECT_DELAY_SEC = float(hr.get("reconnect_delay_seconds", 2.0))

    KNOWN_HRMS = []
    RIDER_BY_MAC = {}
    for _name, monitor in enabled_items(hr.get("monitors", {})):
        mac = str(monitor["mac"]).upper()
        number = int(monitor["number"])
        rider = str(monitor.get("rider_name") or "Unassigned")
        description = str(monitor.get("description") or rider)
        KNOWN_HRMS.append(
            KnownHrm(number, mac, str(monitor.get("device_name") or f"HRM{number}"), description)
        )
        RIDER_BY_MAC[mac] = rider
    LABEL_BY_MAC = {h.mac.upper(): h.label for h in KNOWN_HRMS}
    NUMBER_BY_MAC = {h.mac.upper(): h.number for h in KNOWN_HRMS}
    DEVICE_NAME_BY_MAC = {h.mac.upper(): h.device_name for h in KNOWN_HRMS}
    KNOWN_MACS = set(LABEL_BY_MAC)


# =========================
# Runtime state
# =========================

class StopFlag:
    stop = False


def _sig_handler(*_args) -> None:
    StopFlag.stop = True


@dataclass
class SeenCandidate:
    mac: str
    label: str
    name: str
    rssi: int
    last_seen: float
    device: object


seen: Dict[str, SeenCandidate] = {}
suppressed_until: Dict[str, float] = {}
suppress_reason_by_mac: Dict[str, str] = {}

selected_mac: Optional[str] = None
selected_rssi: Optional[int] = None
active_mac: Optional[str] = None
last_valid_bpm_time: Optional[float] = None
last_pub_time: float = 0.0
last_pub_bpm: Optional[int] = None
last_no_bpm_publish_reason: Optional[str] = None


# =========================
# Helpers
# =========================

def now() -> float:
    return time.time()


def hrm_number_for_mac(mac: Optional[str]) -> Optional[int]:
    if not mac:
        return None
    return NUMBER_BY_MAC.get(mac.upper())


def rider_name_for_mac(mac: Optional[str]) -> str:
    if not mac:
        return ""
    return RIDER_BY_MAC.get(mac.upper(), "Unassigned")


def decode_hrm(data: bytes) -> Tuple[int, Optional[int], List[float]]:
    if not data or len(data) < 2:
        raise ValueError("payload too short")

    flags = data[0]
    hr_16bit = (flags & 0x01) != 0
    ee_present = (flags & 0x08) != 0
    rr_present = (flags & 0x10) != 0

    idx = 1

    if hr_16bit:
        if len(data) < idx + 2:
            raise ValueError("missing 16-bit HR")
        bpm = int(data[idx] | (data[idx + 1] << 8))
        idx += 2
    else:
        bpm = int(data[idx])
        idx += 1

    energy = None
    if ee_present:
        if len(data) < idx + 2:
            raise ValueError("missing energy")
        energy = int(data[idx] | (data[idx + 1] << 8))
        idx += 2

    rr_ms: List[float] = []
    if rr_present:
        while len(data) >= idx + 2:
            rr_raw = int(data[idx] | (data[idx + 1] << 8))
            rr_ms.append((rr_raw / 1024.0) * 1000.0)
            idx += 2

    return bpm, energy, rr_ms


def get_rssi(device, adv) -> Optional[int]:
    rssi = getattr(adv, "rssi", None)
    if rssi is None:
        rssi = getattr(device, "rssi", None)
    if rssi is None:
        return None
    try:
        return int(rssi)
    except Exception:
        return None


def get_name(device, adv) -> str:
    return getattr(adv, "local_name", None) or getattr(device, "name", None) or ""


def is_suppressed(mac: str) -> bool:
    until = suppressed_until.get(mac)
    if until is None:
        return False
    if now() >= until:
        suppressed_until.pop(mac, None)
        suppress_reason_by_mac.pop(mac, None)
        return False
    return True


def suppress_remaining(mac: str) -> float:
    until = suppressed_until.get(mac)
    if until is None:
        return 0.0
    return max(0.0, until - now())


def suppress_hrm(mac: str, seconds: float, reason: str) -> None:
    if seconds <= 0:
        return
    suppressed_until[mac] = now() + seconds
    suppress_reason_by_mac[mac] = reason


def clear_suppression(mac: str) -> None:
    suppressed_until.pop(mac, None)
    suppress_reason_by_mac.pop(mac, None)


def fresh_candidates() -> List[SeenCandidate]:
    t = now()
    rows = [
        c for c in seen.values()
        if (t - c.last_seen) <= SELECTION_FRESH_SEC
        and not is_suppressed(c.mac)
    ]
    return sorted(rows, key=lambda c: c.rssi, reverse=True)


def debug_candidates() -> List[SeenCandidate]:
    t = now()
    rows = [
        c for c in seen.values()
        if (t - c.last_seen) <= SCAN_ACTIVE_WINDOW_SEC
    ]
    return sorted(rows, key=lambda c: c.rssi, reverse=True)


def rssi_for_mac(mac: Optional[str]) -> Optional[int]:
    if not mac:
        return None
    c = seen.get(mac.upper())
    if not c:
        return None
    return c.rssi


def mqtt_connect() -> mqtt.Client:
    c = mqtt.Client(client_id=f"hpr-hr-multi-{TRIKE}")
    c.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    c.will_set(TOPIC_STATUS, payload="offline", qos=1, retain=True)
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    c.loop_start()
    c.publish(TOPIC_STATUS, payload="online", qos=1, retain=True)
    return c


def publish_selected(mqttc: mqtt.Client, mac: Optional[str]) -> None:
    global selected_mac, selected_rssi

    selected_mac = mac.upper() if mac else None
    selected_rssi = rssi_for_mac(selected_mac)

    if selected_mac is None:
        mqttc.publish(TOPIC_SELECTED_MAC, payload="", qos=0, retain=True)
        mqttc.publish(TOPIC_SELECTED_LABEL, payload="", qos=0, retain=True)
        mqttc.publish(TOPIC_SELECTED_HRM_NUMBER, payload="", qos=0, retain=True)
        mqttc.publish(TOPIC_SELECTED_RIDER, payload="", qos=0, retain=True)
        mqttc.publish(TOPIC_SELECTED_RSSI, payload="", qos=0, retain=True)
        return

    mqttc.publish(TOPIC_SELECTED_MAC, payload=selected_mac, qos=0, retain=True)
    mqttc.publish(TOPIC_SELECTED_LABEL, payload=LABEL_BY_MAC.get(selected_mac, selected_mac), qos=0, retain=True)
    mqttc.publish(TOPIC_SELECTED_HRM_NUMBER, payload=str(hrm_number_for_mac(selected_mac) or ""), qos=0, retain=True)
    mqttc.publish(TOPIC_SELECTED_RIDER, payload=rider_name_for_mac(selected_mac), qos=0, retain=True)
    if selected_rssi is not None:
        mqttc.publish(TOPIC_SELECTED_RSSI, payload=str(selected_rssi), qos=0, retain=True)


def publish_no_bpm(mqttc: mqtt.Client, reason: str, force: bool = False) -> None:
    global last_pub_bpm, last_no_bpm_publish_reason, last_pub_time

    if not force and last_no_bpm_publish_reason == reason and last_pub_bpm is None:
        return

    t = now()
    last_pub_bpm = None
    last_no_bpm_publish_reason = reason
    last_pub_time = t

    mqttc.publish(TOPIC_BPM, payload=NO_BPM_PAYLOAD, qos=0, retain=False)
    mqttc.publish(
        TOPIC_JSON,
        payload=json.dumps(
            {
                "bpm": None,
                "display": NO_BPM_PAYLOAD,
                "reason": reason,
                "ts": t,
                "selected_mac": selected_mac,
                "selected_rssi": selected_rssi,
                "active_mac": active_mac,
            },
            separators=(",", ":"),
        ),
        qos=0,
        retain=False,
    )


def publish_bpm(
    mqttc: mqtt.Client,
    mac: str,
    bpm: int,
    energy: Optional[int],
    rr_ms: List[float],
    raw_payload: bytes,
) -> None:
    global last_valid_bpm_time, last_pub_time, last_pub_bpm, last_no_bpm_publish_reason, selected_rssi

    t = now()
    last_valid_bpm_time = t
    selected_rssi = rssi_for_mac(mac) or selected_rssi

    if (t - last_pub_time) < PUBLISH_MIN_INTERVAL:
        return
    if ONLY_ON_CHANGE and last_pub_bpm == bpm:
        return

    last_pub_time = t
    last_pub_bpm = bpm
    last_no_bpm_publish_reason = None

    mqttc.publish(TOPIC_BPM, payload=str(bpm), qos=0, retain=False)
    mqttc.publish(
        TOPIC_JSON,
        payload=json.dumps(
            {
                "bpm": bpm,
                "energy_expended": energy,
                "rr_intervals_ms": rr_ms,
                "raw_hex": raw_payload.hex(),
                "ts": t,
                "mac": mac,
                "hrm_number": hrm_number_for_mac(mac),
                "label": LABEL_BY_MAC.get(mac, mac),
                "rider_name": rider_name_for_mac(mac),
                "active_adapter": ACTIVE_ADAPTER,
                "scan_adapter": SCAN_ADAPTER,
                "selected_mac": selected_mac,
                "selected_rssi": selected_rssi,
                "active_mac": active_mac,
            },
            separators=(",", ":"),
        ),
        qos=0,
        retain=False,
    )


def candidates_json_payload() -> str:
    rows = []
    t = now()

    for c in debug_candidates():
        age = t - c.last_seen
        remaining = suppress_remaining(c.mac)
        rows.append(
            {
                "mac": c.mac,
                "hrm_number": hrm_number_for_mac(c.mac),
                "label": c.label,
                "rider_name": rider_name_for_mac(c.mac),
                "name": c.name,
                "rssi": c.rssi,
                "age_sec": round(age, 2),
                "selection_eligible": age <= SELECTION_FRESH_SEC and remaining <= 0,
                "suppressed": remaining > 0,
                "suppress_reason": suppress_reason_by_mac.get(c.mac),
                "suppress_remaining_sec": round(remaining, 1),
            }
        )

    return json.dumps(rows, separators=(",", ":"))


# =========================
# Async tasks
# =========================

async def scan_for_candidates(mqttc: mqtt.Client) -> None:
    """Run one bounded scan and return only after BlueZ discovery has stopped."""

    def cb(device, adv) -> None:
        mac = (getattr(device, "address", "") or "").upper()
        if mac not in KNOWN_MACS:
            return

        rssi = get_rssi(device, adv)
        if rssi is None:
            return

        seen[mac] = SeenCandidate(
            mac=mac,
            label=LABEL_BY_MAC[mac],
            name=get_name(device, adv),
            rssi=rssi,
            last_seen=now(),
            device=device,
        )

    seen.clear()
    scanner = BleakScanner(cb, adapter=SCAN_ADAPTER)
    mqttc.publish(
        TOPIC_STATUS,
        payload=f"scanner_starting:{SCAN_ADAPTER}:window={SCAN_CYCLE_SEC}s",
        qos=0,
        retain=False,
    )

    try:
        await asyncio.wait_for(scanner.start(), timeout=CONNECT_TIMEOUT_SEC)
        deadline = now() + SCAN_CYCLE_SEC
        while not StopFlag.stop and now() < deadline:
            await asyncio.sleep(0.25)
    finally:
        try:
            await asyncio.wait_for(scanner.stop(), timeout=CONNECT_TIMEOUT_SEC)
        except Exception as e:
            mqttc.publish(
                TOPIC_STATUS,
                payload=f"scanner_stop_error:{type(e).__name__}:{e}",
                qos=0,
                retain=False,
            )

    mqttc.publish(
        TOPIC_STATUS,
        payload=f"scanner_stopped:{SCAN_ADAPTER}:candidates={len(fresh_candidates())}",
        qos=0,
        retain=False,
    )
    await asyncio.sleep(BLE_SETTLE_SEC)


async def candidates_debug_task(mqttc: mqtt.Client) -> None:
    while not StopFlag.stop:
        try:
            mqttc.publish(TOPIC_CANDIDATES_JSON, payload=candidates_json_payload(), qos=0, retain=False)
            if selected_mac is not None:
                rssi = rssi_for_mac(selected_mac)
                if rssi is not None:
                    mqttc.publish(TOPIC_SELECTED_RSSI, payload=str(rssi), qos=0, retain=True)
        except Exception as e:
            mqttc.publish(TOPIC_STATUS, payload=f"debug_publish_error:{type(e).__name__}:{e}", qos=0, retain=False)
        await asyncio.sleep(2.0)


async def connect_and_validate_candidate(mqttc: mqtt.Client, mac: str) -> bool:
    """
    Try one HRM. Return True only after the HRM has connected and produced
    valid non-zero BPM. Once True, this coroutine stays in the active monitoring
    loop until heartbeat is lost, the device disconnects, or shutdown is requested.
    It then returns False so the manager searches again.
    """
    global active_mac, last_valid_bpm_time, last_pub_time, last_pub_bpm

    mac = mac.upper()
    client: Optional[BleakClient] = None
    notify_started = False
    connection_slot = None

    try:
        connection_slot = await acquire_connection_slot(ACTIVE_ADAPTER)
        mqttc.publish(TOPIC_STATUS, payload=f"finding:{mac}", qos=0, retain=False)
        # Candidate ranking comes from the USB scan controller, but a BLEDevice
        # is adapter-specific. Rediscover the selected MAC on the onboard
        # controller before creating the GATT client.
        dev = await asyncio.wait_for(
            BleakScanner.find_device_by_address(
                mac,
                timeout=FIND_TIMEOUT_SEC,
                adapter=ACTIVE_ADAPTER,
            ),
            timeout=FIND_TIMEOUT_SEC + 1,
        )

        if dev is None:
            mqttc.publish(TOPIC_STATUS, payload=f"not_found_on_{ACTIVE_ADAPTER}:{mac}", qos=0, retain=False)
            publish_no_bpm(mqttc, f"not_found:{mac}", force=True)
            suppress_hrm(mac, SUPPRESS_FIND_FAIL_SEC, "not_found")
            return False

        mqttc.publish(TOPIC_STATUS, payload=f"connecting:{mac}", qos=0, retain=False)

        valid_bpm_event = asyncio.Event()
        zero_bpm_count = 0

        client = BleakClient(dev, timeout=CONNECT_TIMEOUT_SEC)
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT_SEC)
        if not client.is_connected:
            raise RuntimeError("connect failed")

        last_valid_bpm_time = None
        last_pub_time = 0.0
        last_pub_bpm = None

        mqttc.publish(TOPIC_STATUS, payload=f"connected:{mac}", qos=0, retain=False)
        # A successful GATT connection is the rider lock. Publish the mapped
        # identity immediately; BPM validation remains a separate step.
        publish_selected(mqttc, mac)

        def handler(_sender: int, payload: bytearray) -> None:
            nonlocal zero_bpm_count
            global active_mac

            try:
                bpm, energy, rr_ms = decode_hrm(bytes(payload))
            except Exception as e:
                mqttc.publish(TOPIC_STATUS, payload=f"decode_error:{type(e).__name__}:{e}", qos=0, retain=False)
                return

            if bpm == 0:
                zero_bpm_count += 1
                mqttc.publish(TOPIC_STATUS, payload=f"zero_bpm:{mac}:count={zero_bpm_count}", qos=0, retain=False)
                publish_no_bpm(mqttc, f"zero_bpm:{mac}")
                return

            if bpm < BPM_MIN or bpm > BPM_MAX:
                mqttc.publish(TOPIC_STATUS, payload=f"invalid_bpm:{bpm}:{mac}", qos=0, retain=False)
                publish_no_bpm(mqttc, f"invalid_bpm:{mac}")
                return

            if not valid_bpm_event.is_set():
                active_mac = mac
                mqttc.publish(TOPIC_STATUS, payload=f"active:{mac}", qos=0, retain=False)

            zero_bpm_count = 0
            clear_suppression(mac)
            publish_bpm(mqttc, mac, bpm, energy, rr_ms, bytes(payload))
            valid_bpm_event.set()

        await asyncio.wait_for(
            client.start_notify(HRM_CHR, handler),
            timeout=CONNECT_TIMEOUT_SEC,
        )
        notify_started = True
        release_connection_slot(connection_slot)
        connection_slot = None

        # Candidate proof window.
        mqttc.publish(
            TOPIC_STATUS,
            payload=f"prove_bpm:{mac}:timeout={CANDIDATE_BPM_PROVE_SEC}s",
            qos=0,
            retain=False,
        )

        prove_start = now()
        while not StopFlag.stop and client.is_connected:
            if valid_bpm_event.is_set():
                break

            if zero_bpm_count >= 3:
                # Still allow the full proof window, but keep showing dash.
                publish_no_bpm(mqttc, f"zero_bpm_waiting:{mac}")

            if (now() - prove_start) >= CANDIDATE_BPM_PROVE_SEC:
                mqttc.publish(
                    TOPIC_STATUS,
                    payload=f"candidate_no_valid_bpm:{mac}:suppressed_for={SUPPRESS_NO_BPM_SEC}s",
                    qos=0,
                    retain=False,
                )
                publish_no_bpm(mqttc, f"candidate_no_valid_bpm:{mac}", force=True)
                suppress_hrm(mac, SUPPRESS_NO_BPM_SEC, "candidate_no_valid_bpm")
                return False

            await asyncio.sleep(0.25)

        if not valid_bpm_event.is_set():
            publish_no_bpm(mqttc, f"candidate_disconnected_before_bpm:{mac}", force=True)
            suppress_hrm(mac, SUPPRESS_CONNECTION_ERROR_SEC, "disconnect_before_valid_bpm")
            return False

        # Active monitoring loop. Scanning stays off while valid BPM is fresh.
        while not StopFlag.stop and client.is_connected:
            if last_valid_bpm_time is None:
                elapsed = now() - prove_start
            else:
                elapsed = now() - last_valid_bpm_time

            if elapsed >= HEARTBEAT_LOST_SEC:
                mqttc.publish(
                    TOPIC_STATUS,
                    payload=f"heartbeat_lost:{mac}:elapsed={elapsed:.1f}s:suppressed_for={SUPPRESS_NO_BPM_SEC}s",
                    qos=0,
                    retain=False,
                )
                publish_no_bpm(mqttc, f"heartbeat_lost:{mac}", force=True)
                suppress_hrm(mac, SUPPRESS_NO_BPM_SEC, "heartbeat_lost")
                return False

            await asyncio.sleep(0.5)

        mqttc.publish(TOPIC_STATUS, payload=f"disconnected:{mac}", qos=0, retain=False)
        publish_no_bpm(mqttc, f"disconnected:{mac}", force=True)
        suppress_hrm(mac, SUPPRESS_CONNECTION_ERROR_SEC, "disconnected")
        return False

    except Exception as e:
        mqttc.publish(TOPIC_STATUS, payload=f"connection_error:{type(e).__name__}:{e}", qos=0, retain=False)
        publish_no_bpm(mqttc, f"connection_error:{type(e).__name__}:{mac}", force=True)
        suppress_hrm(mac, SUPPRESS_CONNECTION_ERROR_SEC, f"connection_error:{type(e).__name__}")
        return False

    finally:
        release_connection_slot(connection_slot)
        if client is not None:
            if notify_started and client.is_connected:
                try:
                    await asyncio.wait_for(client.stop_notify(HRM_CHR), timeout=5.0)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except Exception:
                pass

        if active_mac == mac:
            active_mac = None
        publish_selected(mqttc, None)
        await asyncio.sleep(BLE_SETTLE_SEC)


async def manager_task(mqttc: mqtt.Client) -> None:
    while not StopFlag.stop:
        publish_selected(mqttc, None)
        try:
            await scan_for_candidates(mqttc)
        except Exception as e:
            mqttc.publish(
                TOPIC_STATUS,
                payload=f"scanner_error:{type(e).__name__}:{e}",
                qos=0,
                retain=False,
            )
            publish_no_bpm(mqttc, f"scanner_error:{type(e).__name__}")
            await asyncio.sleep(RECONNECT_DELAY_SEC)
            continue

        candidates = fresh_candidates()

        if not candidates:
            mqttc.publish(TOPIC_STATUS, payload="waiting_for_hrm_candidate", qos=0, retain=False)
            publish_no_bpm(mqttc, "waiting_for_hrm_candidate")
            await asyncio.sleep(RECONNECT_DELAY_SEC)
            continue

        # Try candidates in current strongest-RSSI order.
        tried_any = False
        for candidate in candidates:
            if StopFlag.stop:
                break

            if is_suppressed(candidate.mac):
                continue

            tried_any = True
            mqttc.publish(
                TOPIC_STATUS,
                payload=f"candidate_try:{candidate.mac}:hrm={hrm_number_for_mac(candidate.mac)}:rssi={candidate.rssi}",
                qos=0,
                retain=False,
            )

            await connect_and_validate_candidate(mqttc, candidate.mac)

            # After each candidate attempt or active session ends, run a fresh
            # bounded scan rather than continuing an old candidate list.
            break

        if not tried_any:
            publish_no_bpm(mqttc, "no_unsuppressed_candidates")
            await asyncio.sleep(RECONNECT_DELAY_SEC)
        else:
            await asyncio.sleep(0.2)


async def main() -> None:
    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    mqttc = mqtt_connect()
    mqttc.publish(TOPIC_STATUS, payload="multi_hrm_v17_starting", qos=1, retain=True)
    mqttc.publish(TOPIC_JSON, payload=json.dumps({"event": "multi_hrm_v17_starting", "ts": now()}), qos=0, retain=False)

    tasks = [
        asyncio.create_task(candidates_debug_task(mqttc)),
        asyncio.create_task(manager_task(mqttc)),
    ]

    try:
        while not StopFlag.stop:
            await asyncio.sleep(0.5)
    finally:
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            publish_no_bpm(mqttc, "offline", force=True)
            publish_selected(mqttc, None)
            mqttc.publish(TOPIC_STATUS, payload="offline", qos=1, retain=True)
        except Exception:
            pass

        mqttc.loop_stop()


if __name__ == "__main__":
    parser = config_arg_parser("Publish HPR BLE heart-rate telemetry")
    args = parser.parse_args()
    configure(load_config(args.config))
    asyncio.run(main())

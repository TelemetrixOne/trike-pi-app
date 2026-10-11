"""Recover the dedicated HRM scan controller without resetting other USB devices."""

from __future__ import annotations

import argparse
import logging
import re
import signal
import subprocess
import time
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt

from hpr_gateway.bluetooth import resolve_bluetooth_role
from hpr_gateway.config import ConfigError, load_config


LOG = logging.getLogger("hpr_bluetooth_recovery")
SYS_BLUETOOTH = Path("/sys/class/bluetooth")
POLL_SECONDS = 10
RETRY_COOLDOWN_SECONDS = 30
MAX_RETRY_COOLDOWN_SECONDS = 120


def scan_command_failed(message: str, adapter: str) -> bool:
    """Only errors from this controller's scan commands qualify."""
    if not re.search(rf"\b{re.escape(adapter)}:", message):
        return False
    return any(text in message for text in (
        "Unable to disable scanning", "Unable to enable scanning",
        "stop background scanning failed", "Opcode 0x200c failed",
        "Opcode 0x200b failed", "Opcode 0x2041 failed", "Opcode 0x2042 failed",
    ))


def kernel_scan_failed(adapter: str, since: float) -> bool:
    result = subprocess.run(
        ["journalctl", "-k", "--since", f"@{since:.3f}", "--no-pager", "-o", "cat"],
        capture_output=True, text=True, timeout=6, check=True,
    )
    return sum(scan_command_failed(line, adapter) for line in result.stdout.splitlines()) >= 3


def refresh_hrm_scanner() -> None:
    # SIGUSR1 rebuilds discovery alone; the onboard rider connection survives.
    active = subprocess.run(["systemctl", "is-active", "--quiet", "hpr-heart-rate.service"],
                            timeout=6).returncode == 0
    command = (["systemctl", "kill", "--kill-whom=main", "--signal=SIGUSR1"] if active
               else ["systemctl", "restart"])
    subprocess.run(command + ["hpr-heart-rate.service"], timeout=20, check=True)


class ScanHealth:
    """Watch the HRM service's own scan results, not just HCI power state."""

    def __init__(self, config):
        self.failures: deque[float] = deque()
        self.error_pending = False
        self.client = mqtt.Client(client_id=f"hpr-hr-scan-watch-{config.trike_id}")
        self.client.username_pw_set(config.mqtt.username, config.mqtt.password)
        self.topic = config.topic("hr", "status")
        self.client.on_connect = lambda client, _userdata, _flags, result: (
            client.subscribe(self.topic) if result == 0 else
            LOG.error("Could not subscribe to HRM scan status: MQTT code %s", result)
        )
        self.client.on_message = self._on_message
        self.client.connect(config.mqtt.host, config.mqtt.port, keepalive=30)
        self.client.loop_start()

    def _on_message(self, _client, _userdata, message):
        self.observe(message.payload.decode("utf-8", errors="replace"))

    def observe(self, status: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        while self.failures and now - self.failures[0] > 120:
            self.failures.popleft()
        if status.startswith("scanner_error:"):
            self.failures.append(now)
            self.error_pending = False
        elif status.startswith("scanner_stop_error:"):
            self.error_pending = True
        elif status.startswith("scanner_stopped:"):
            if status.endswith("candidates=0") and self.error_pending:
                self.failures.append(now)
            elif not status.endswith("candidates=0"):
                self.failures.clear()
            self.error_pending = False
        elif status.startswith("candidate_try:"):
            self.failures.clear()
            self.error_pending = False

    def failed(self) -> bool:
        now = time.monotonic()
        while self.failures and now - self.failures[0] > 120:
            self.failures.popleft()
        return len(self.failures) >= 3

    def clear(self) -> None:
        self.failures.clear()
        self.error_pending = False

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def adapter_is_up(adapter: str) -> bool:
    result = subprocess.run(
        ["hciconfig", adapter], capture_output=True, text=True, timeout=6, check=True
    )
    return "UP RUNNING" in result.stdout


def usb_device_for_interface(interface: Path) -> Path:
    # The immediate parent of a USB interface is its own device. Never walk
    # farther up the tree: that could deauthorize a shared hub and its peers.
    candidate = interface.parent
    if (all((candidate / name).is_file() for name in ("idVendor", "idProduct", "authorized"))
            and (candidate / "bDeviceClass").read_text(encoding="ascii").strip().lower() != "09"):
        return candidate
    raise ConfigError("HCI interface is not backed by an individually resettable USB device")


def usb_device_for_adapter(adapter: str, sys_bluetooth: Path = SYS_BLUETOOTH) -> Path:
    """Resolve only the USB device that owns this configured HCI controller."""
    interface = (sys_bluetooth / adapter / "device").resolve(strict=True)
    return usb_device_for_interface(interface)


def reauthorize_usb_device(device: Path) -> None:
    authorized = device / "authorized"
    vendor = (device / "idVendor").read_text(encoding="ascii").strip()
    product = (device / "idProduct").read_text(encoding="ascii").strip()
    LOG.warning("Re-enumerating only USB device %s (%s:%s)", device.name, vendor, product)
    authorized.write_text("0", encoding="ascii")
    try:
        time.sleep(2)
    finally:
        authorized.write_text("1", encoding="ascii")


def power_up(adapter: str) -> bool:
    try:
        subprocess.run(["hciconfig", adapter, "up"], capture_output=True, text=True,
                       timeout=12, check=True)
        return adapter_is_up(adapter)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOG.warning("Could not power up %s: %s", adapter, exc)
        return False


def wait_for_controller(config, previous_adapter: str, timeout_seconds: int = 20,
                        restart_hrm: bool = False) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            adapter = resolve_bluetooth_role(config, "heart_rate", default_adapter="hci1")
            if adapter_is_up(adapter) or power_up(adapter):
                if adapter != previous_adapter or restart_hrm:
                    LOG.warning("Scan controller recovered (%s -> %s); refreshing HRM discovery",
                                previous_adapter, adapter)
                    refresh_hrm_scanner()
                return True
        except (ConfigError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    return False


def recover(config, adapter: str, *, force_usb_reset: bool = False) -> bool:
    if not force_usb_reset and power_up(adapter):
        LOG.info("Recovered %s without a USB reset", adapter)
        return True
    device = usb_device_for_adapter(adapter)
    reauthorize_usb_device(device)
    # Any USB re-enumeration invalidates BlueZ scanner state, even when Linux
    # reuses the same hci number. Rebuild scanning without dropping active GATT.
    if wait_for_controller(config, adapter, restart_hrm=True):
        LOG.info("Recovered HRM scan controller after isolated USB reset")
        return True
    LOG.error("HRM scan controller did not recover after isolated USB reset")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover the dedicated HRM scan controller")
    parser.add_argument("--config", default="/etc/hpr/hpr.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_attempt = 0.0
    last_recovery_finished = time.time()
    failed_recoveries = 0
    scan_health = None
    while not stopping:
        try:
            config = load_config(args.config)
            if not config.enabled("heart_rate"):
                time.sleep(POLL_SECONDS)
                continue
            if scan_health is None:
                try:
                    scan_health = ScanHealth(config)
                except (OSError, ValueError) as exc:
                    LOG.error("HRM scan status subscription unavailable: %s", exc)
            adapter = resolve_bluetooth_role(config, "heart_rate", default_adapter="hci1")
            is_up = adapter_is_up(adapter)
            scan_failed = scan_health is not None and scan_health.failed()
            kernel_failed = kernel_scan_failed(adapter, max(last_recovery_finished, time.time() - 30))
            if is_up and not scan_failed and not kernel_failed:
                time.sleep(POLL_SECONDS)
                continue
            now = time.monotonic()
            if now >= next_attempt:
                LOG.warning("HRM scan adapter %s is %s; attempting isolated recovery",
                            adapter, "DOWN" if not is_up else "failing scan commands" if kernel_failed
                            else "repeatedly failing discovery")
                recovered = recover(config, adapter, force_usb_reset=is_up)
                failed_recoveries = 0 if recovered else failed_recoveries + 1
                next_attempt = time.monotonic() + min(
                    MAX_RETRY_COOLDOWN_SECONDS,
                    RETRY_COOLDOWN_SECONDS * 2 ** min(failed_recoveries, 2),
                )
                last_recovery_finished = time.time()
                if scan_health:
                    scan_health.clear()
        except (ConfigError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            LOG.error("HRM scan health check failed: %s", exc)
        time.sleep(POLL_SECONDS)
    if scan_health:
        scan_health.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Recover the dedicated HRM scan controller without resetting other USB devices."""

from __future__ import annotations

import argparse
import logging
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
POLL_SECONDS = 15
RETRY_COOLDOWN_SECONDS = 120
MAX_USB_RESETS_PER_HOUR = 3


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


def adapter_responds(adapter: str) -> bool:
    """A verbose local-controller query detects a powered but wedged USB HCI."""
    try:
        subprocess.run(["hciconfig", adapter, "-a"], capture_output=True,
                       text=True, timeout=8, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOG.warning("Scan adapter %s did not answer controller query: %s", adapter, exc)
        return False


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
                    LOG.warning("Scan controller recovered (%s -> %s); restarting HRM service only",
                                previous_adapter, adapter)
                    subprocess.run(["systemctl", "try-restart", "hpr-heart-rate.service"],
                                   timeout=20, check=True)
                return True
        except (ConfigError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    return False


def recover(config, adapter: str, *, force_usb_reset: bool = False,
            restart_hrm: bool = False) -> bool:
    if not force_usb_reset and power_up(adapter):
        LOG.info("Recovered %s without a USB reset", adapter)
        return True
    device = usb_device_for_adapter(adapter)
    reauthorize_usb_device(device)
    if wait_for_controller(config, adapter, restart_hrm=restart_hrm):
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
    attempts: deque[float] = deque()
    next_attempt = 0.0
    unresponsive_checks = 0
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
            unresponsive_checks = 0 if not is_up or adapter_responds(adapter) else unresponsive_checks + 1
            scan_failed = scan_health is not None and scan_health.failed()
            if is_up and unresponsive_checks < 3 and not scan_failed:
                time.sleep(POLL_SECONDS)
                continue
            now = time.monotonic()
            while attempts and now - attempts[0] >= 3600:
                attempts.popleft()
            if now >= next_attempt and len(attempts) < MAX_USB_RESETS_PER_HOUR:
                LOG.warning("HRM scan adapter %s is %s; attempting isolated recovery",
                            adapter, "DOWN" if not is_up else
                            "repeatedly failing discovery" if scan_failed else "unresponsive")
                attempts.append(now)
                next_attempt = now + RETRY_COOLDOWN_SECONDS
                recover(config, adapter, force_usb_reset=is_up,
                        restart_hrm=scan_failed)
                unresponsive_checks = 0
                if scan_health:
                    scan_health.clear()
            elif now >= next_attempt and len(attempts) >= MAX_USB_RESETS_PER_HOUR:
                LOG.error("HRM scan adapter remains DOWN; recovery limit reached (hardware review needed)")
                next_attempt = now + RETRY_COOLDOWN_SECONDS
        except (ConfigError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            LOG.error("HRM scan health check failed: %s", exc)
        time.sleep(POLL_SECONDS)
    if scan_health:
        scan_health.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

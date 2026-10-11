import tempfile
import unittest
from collections import deque
from pathlib import Path
import subprocess
from unittest.mock import patch

from hpr_gateway.services import bluetooth_recovery as recovery


class BluetoothRecoveryTests(unittest.TestCase):
    def test_kernel_errors_detect_powered_scanner_stall_and_ignore_other_adapter(self):
        messages = "\n".join([
            "Bluetooth: hci1: Opcode 0x200c failed: -16",
            "Bluetooth: hci1: Unable to disable scanning: -16",
            "Bluetooth: hci1: stop background scanning failed: -16",
        ])
        with patch.object(recovery.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, messages)):
            self.assertTrue(recovery.kernel_scan_failed("hci1", 100))
            self.assertFalse(recovery.kernel_scan_failed("hci0", 100))
        self.assertFalse(recovery.scan_command_failed("Bluetooth: hci10: Opcode 0x200c failed", "hci1"))
        self.assertFalse(recovery.scan_command_failed("Bluetooth: hci1: BCM: firmware Patch file not found", "hci1"))

    def test_recovery_refreshes_scanning_without_restarting_active_gatt(self):
        with patch.object(recovery.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0)) as run:
            recovery.refresh_hrm_scanner()
        self.assertEqual(run.call_args_list[-1].args[0],
                         ["systemctl", "kill", "--kill-whom=main", "--signal=SIGUSR1", "hpr-heart-rate.service"])

    def test_recovery_starts_failed_hrm_service(self):
        with patch.object(recovery.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 3), subprocess.CompletedProcess([], 0)
        ]) as run:
            recovery.refresh_hrm_scanner()
        self.assertEqual(run.call_args_list[-1].args[0], ["systemctl", "restart", "hpr-heart-rate.service"])

    def test_repeated_failed_discovery_is_unhealthy_but_empty_scans_alone_are_not(self):
        health = recovery.ScanHealth.__new__(recovery.ScanHealth)
        health.failures = deque()
        health.error_pending = False
        for instant in (0, 15, 30):
            health.observe("scanner_stopped:hci1:candidates=0", instant)
        self.assertEqual(len(health.failures), 0)
        for instant in (40, 55, 70):
            health.observe("scanner_stop_error:BleakDBusError:InProgress", instant)
            health.observe("scanner_stopped:hci1:candidates=0", instant)
        self.assertEqual(len(health.failures), 3)
        health.observe("candidate_try:device", 75)
        self.assertEqual(len(health.failures), 0)

    def test_selects_only_the_usb_device_owning_the_scanner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "usb1" / "1-1" / "1-1.4"
            interface = device / "interface-0"
            interface.mkdir(parents=True)
            (device / "idVendor").write_text("0b05", encoding="ascii")
            (device / "idProduct").write_text("17cb", encoding="ascii")
            (device / "authorized").write_text("1", encoding="ascii")
            (device / "bDeviceClass").write_text("e0", encoding="ascii")
            self.assertEqual(recovery.usb_device_for_interface(interface), device)
            with patch.object(recovery.time, "sleep"):
                recovery.reauthorize_usb_device(device)
            self.assertEqual((device / "authorized").read_text(encoding="ascii"), "1")

    def test_never_falls_back_to_shared_usb_hub(self):
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary) / "1-1"
            interface = hub / "1-1.4" / "interface-0"
            interface.mkdir(parents=True)
            for name, value in (("idVendor", "1234"), ("idProduct", "5678"),
                                ("authorized", "1"), ("bDeviceClass", "09")):
                (hub / name).write_text(value, encoding="ascii")
            with self.assertRaises(recovery.ConfigError):
                recovery.usb_device_for_interface(interface)

    def test_tries_power_up_before_targeted_usb_reset(self):
        with patch.object(recovery, "power_up", return_value=True), \
             patch.object(recovery, "reauthorize_usb_device") as reset:
            self.assertTrue(recovery.recover(object(), "hci1"))
            reset.assert_not_called()

    def test_usb_authorization_is_restored_if_reset_wait_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            device = Path(temporary)
            (device / "idVendor").write_text("0b05", encoding="ascii")
            (device / "idProduct").write_text("17cb", encoding="ascii")
            authorized = device / "authorized"
            authorized.write_text("1", encoding="ascii")
            with patch.object(recovery.time, "sleep", side_effect=RuntimeError("interrupted")):
                with self.assertRaises(RuntimeError):
                    recovery.reauthorize_usb_device(device)
            self.assertEqual(authorized.read_text(encoding="ascii"), "1")

    def test_reenumeration_is_followed_by_role_check(self):
        with patch.object(recovery, "power_up", return_value=False), \
             patch.object(recovery, "usb_device_for_adapter", return_value=Path("/sys/usb/adapter")), \
             patch.object(recovery, "reauthorize_usb_device") as reset, \
             patch.object(recovery, "wait_for_controller", return_value=True) as wait:
            self.assertTrue(recovery.recover(object(), "hci1"))
            reset.assert_called_once_with(Path("/sys/usb/adapter"))
            wait.assert_called_once()

    def test_powered_but_unresponsive_controller_forces_own_usb_reset(self):
        with patch.object(recovery, "power_up") as power, \
             patch.object(recovery, "usb_device_for_adapter", return_value=Path("/sys/usb/adapter")), \
             patch.object(recovery, "reauthorize_usb_device") as reset, \
             patch.object(recovery, "wait_for_controller", return_value=True):
            self.assertTrue(recovery.recover(object(), "hci1", force_usb_reset=True))
            power.assert_not_called()
            reset.assert_called_once_with(Path("/sys/usb/adapter"))


if __name__ == "__main__":
    unittest.main()

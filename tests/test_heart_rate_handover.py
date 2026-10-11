import asyncio
import subprocess
import unittest
from unittest.mock import patch

from hpr_gateway.services import heart_rate as hr


CONNECTIONS = "Connections:\n\t< LE CA:2E:62:E3:CC:A8 handle 66 state 1 lm CENTRAL\n"
RSSI_REPLY = "< HCI Command: ogf 0x05, ocf 0x0005, plen 2\n  42 00\n> HCI Event: 0x0e plen 7\n  01 05 14 00 42 00 C4\n"


class HandoverTests(unittest.TestCase):
    def test_reads_live_rssi_for_the_connected_hrm_only(self):
        with patch.object(hr.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, CONNECTIONS),
            subprocess.CompletedProcess([], 0, RSSI_REPLY),
        ]) as run:
            self.assertEqual(hr.read_connection_rssi("hci0", "CA:2E:62:E3:CC:A8"), -60)
            self.assertEqual(run.call_args_list[0].args[0], ["hcitool", "-i", "hci0", "con"])
            self.assertEqual(run.call_args_list[1].args[0][-2:], ["0x42", "0x00"])
        self.assertIsNone(hr.parse_read_rssi(RSSI_REPLY, 67))

    def test_only_a_fresh_configured_stronger_rival_is_eligible(self):
        current = hr.SeenCandidate("AA:AA:AA:AA:AA:AA", "current", "", -64, 100, object())
        weak = hr.SeenCandidate("BB:BB:BB:BB:BB:BB", "weak", "", -60, 100, object())
        strong = hr.SeenCandidate("CC:CC:CC:CC:CC:CC", "strong", "", -48, 100, object())
        with patch.object(hr, "fresh_candidates", return_value=[weak, strong, current]), \
             patch.object(hr, "HANDOVER_MARGIN_DB", 8):
            self.assertEqual(hr.stronger_challenger(current.mac, -60), strong)
            self.assertIsNone(hr.stronger_challenger(current.mac, None))
            self.assertIsNone(hr.stronger_challenger(current.mac, -45))

    def test_handover_requires_sustained_same_rival(self):
        one = hr.SeenCandidate("BB:BB:BB:BB:BB:BB", "one", "", -48, 100, object())
        two = hr.SeenCandidate("CC:CC:CC:CC:CC:CC", "two", "", -47, 100, object())
        tracker = hr.HandoverTracker()
        with patch.object(hr, "HANDOVER_CONFIRM_SEC", 6):
            self.assertIsNone(tracker.observe(one, True, 100))
            self.assertIsNone(tracker.observe(one, True, 104))
            self.assertIsNone(tracker.observe(two, True, 105))
            self.assertIsNone(tracker.observe(two, False, 112))
            self.assertIsNone(tracker.observe(one, True, 113))
            self.assertEqual(tracker.observe(one, True, 119), one.mac)


class ContinuousScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_signal_rebuilds_usb_scanner_without_changing_active_rider(self):
        instances = []

        class FakeScanner:
            def __init__(self, callback, adapter):
                self.stopped = False
                instances.append(self)

            async def start(self):
                pass

            async def stop(self):
                self.stopped = True

        class FakeMqtt:
            def publish(self, *_args, **_kwargs):
                pass

        ready = asyncio.Event()
        hr.StopFlag.stop = False
        with patch.object(hr, "BleakScanner", FakeScanner), \
             patch.object(hr, "RECONNECT_DELAY_SEC", 0.01), \
             patch.object(hr, "RUNTIME_CONFIG", None), \
             patch.object(hr, "active_mac", "AA:AA:AA:AA:AA:AA"):
            task = asyncio.create_task(hr.scanner_task(FakeMqtt(), ready))
            try:
                await asyncio.wait_for(ready.wait(), 1)
                hr._scan_restart_handler()
                async def restarted():
                    while len(instances) < 2:
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(restarted(), 2)
                self.assertTrue(instances[0].stopped)
                self.assertFalse(instances[1].stopped)
                self.assertEqual(hr.active_mac, "AA:AA:AA:AA:AA:AA")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_usb_scan_remains_active_until_service_shutdown(self):
        class FakeScanner:
            instance = None

            def __init__(self, callback, adapter):
                self.adapter = adapter
                self.stopped = False
                FakeScanner.instance = self

            async def start(self):
                return None

            async def stop(self):
                self.stopped = True

        class FakeMqtt:
            def publish(self, *_args, **_kwargs):
                return None

        ready = asyncio.Event()
        hr.StopFlag.stop = False
        with patch.object(hr, "BleakScanner", FakeScanner), patch.object(hr, "SCAN_ADAPTER", "hci1"):
            task = asyncio.create_task(hr.scanner_task(FakeMqtt(), ready))
            await asyncio.wait_for(ready.wait(), 1)
            self.assertFalse(FakeScanner.instance.stopped)
            self.assertEqual(FakeScanner.instance.adapter, "hci1")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(FakeScanner.instance.stopped)
        hr.StopFlag.stop = False


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

bleak = types.ModuleType("bleak")
bleak.BleakClient = object
bleak.BleakScanner = object
sys.modules.setdefault("bleak", bleak)

from hpr_gateway.services.power_cadence import (  # noqa: E402
    PedalStreamStale,
    bounded,
)


class PowerCadenceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_operation_times_out_with_context(self) -> None:
        async def blocked():
            await asyncio.sleep(1)

        with self.assertRaisesRegex(TimeoutError, "GATT read timed out"):
            await bounded(blocked(), 0.01, "GATT read")

    def test_stale_stream_has_a_dedicated_reconnect_error(self) -> None:
        self.assertTrue(issubclass(PedalStreamStale, RuntimeError))

    def test_all_ble_operations_and_stale_data_are_bounded(self) -> None:
        source = (
            ROOT / "hpr_gateway" / "services" / "power_cadence.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"BLE scan"', source)
        self.assertIn('"BLE connect"', source)
        self.assertIn('"GATT notification subscription"', source)
        self.assertIn('"battery GATT read"', source)
        self.assertIn("now - last_notification > stale_timeout", source)
        self.assertIn("reconnect_delay = min(reconnect_max, reconnect_delay * 2)", source)


if __name__ == "__main__":
    unittest.main()

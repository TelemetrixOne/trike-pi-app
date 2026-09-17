from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("gps", types.ModuleType("gps"))

from hpr_gateway.services.gps import (  # noqa: E402
    build_nav_payload,
    is_real_receiver_report,
    reconnect_delay,
)


class FakeConfig:
    trike_id = "trike1"


class GpsRecoveryTests(unittest.TestCase):
    def test_only_receiver_reports_mark_stream_recovered(self) -> None:
        for report_class in ("TPV", "SKY", "GST", "ATT"):
            self.assertTrue(is_real_receiver_report(report_class))
        for report_class in ("VERSION", "WATCH", "DEVICES", None, ""):
            self.assertFalse(is_real_receiver_report(report_class))

    def test_reconnect_backoff_is_exponential_and_capped(self) -> None:
        delays = [reconnect_delay(attempt, 1.0, 15.0) for attempt in range(1, 7)]
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 15.0, 15.0])

    def test_existing_nav_contract_is_preserved(self) -> None:
        published_at = datetime(2026, 7, 19, 4, 5, 7, tzinfo=timezone.utc)
        payload = build_nav_payload(
            FakeConfig(),
            {
                "class": "TPV",
                "mode": 3,
                "time": "2026-07-19T04:05:06Z",
                "lat": -37.951,
                "lon": 145.242,
                "speed": 10.0,
                "track": 361.0,
            },
            42,
            published_at,
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["schema"], "hpr.nav.v1")
        self.assertEqual(payload["source"], "pi_gpsd")
        self.assertEqual(payload["trike_id"], "trike1")
        self.assertEqual(payload["seq"], 42)
        self.assertEqual(payload["speed_mps"], 10.0)
        self.assertEqual(payload["speed_kmh"], 36.0)
        self.assertEqual(payload["course_deg"], 1.0)
        self.assertEqual(payload["fix_age_ms"], 1000)


if __name__ == "__main__":
    unittest.main()

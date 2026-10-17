from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("gps", types.ModuleType("gps"))

from hpr_gateway.services.gps import (  # noqa: E402
    build_nav_payload,
    is_real_receiver_report,
    reconnect_delay,
    ReceiverHealth,
    valid_position,
)
from hpr_gateway.services import gps as gps_service


class FakeConfig:
    trike_id = "trike1"


class GpsRecoveryTests(unittest.TestCase):
    def test_sky_only_session_reconnects_and_mqtt_startup_is_async(self):
        class Finished(BaseException):
            pass

        clock = [0.0]
        class Session:
            def waiting(self, timeout):
                clock[0] += 1
                if clock[0] >= 38:
                    raise Finished()
                return True
            def next(self):
                return {"class": "SKY"}
            def close(self):
                pass

        config = Mock(trike_id="test-trike")
        config.get.side_effect = lambda key, default=None: default
        config.resolve_topic.side_effect = lambda value, *parts: "/".join(parts)
        client = Mock()
        with patch.object(sys, "argv", ["gps"]), \
             patch.object(gps_service, "load_config", return_value=config), \
             patch.object(gps_service, "make_client", return_value=client), \
             patch.object(gps_service.gps, "gps", side_effect=lambda **_: Session(), create=True) as open_session, \
             patch.object(gps_service.gps, "WATCH_ENABLE", 1, create=True), \
             patch.object(gps_service.gps, "WATCH_NEWSTYLE", 2, create=True), \
             patch.object(gps_service.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(gps_service.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            with self.assertRaises(Finished):
                gps_service.main()
        self.assertGreaterEqual(open_session.call_count, 2)
        client.connect_async.assert_called_once()
        client.max_queued_messages_set.assert_called_once_with(100)
        self.assertTrue(all(call.args[0] != "nav" for call in client.publish.call_args_list))

    def test_only_receiver_reports_mark_stream_recovered(self) -> None:
        for report_class in ("TPV", "SKY", "GST", "ATT"):
            self.assertTrue(is_real_receiver_report(report_class))
        for report_class in ("VERSION", "WATCH", "DEVICES", None, ""):
            self.assertFalse(is_real_receiver_report(report_class))

    def test_reconnect_backoff_is_exponential_and_capped(self) -> None:
        delays = [reconnect_delay(attempt, 1.0, 15.0) for attempt in range(1, 7)]
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 15.0, 15.0])
        self.assertEqual(reconnect_delay(100000, 1, 15), 15)

    def test_fix_requires_mode_coordinates_and_fresh_time(self):
        wall = datetime(2026, 10, 17, tzinfo=timezone.utc)
        report = {"class": "TPV", "mode": 3, "lat": -38, "lon": 145, "time": wall.isoformat()}
        self.assertTrue(valid_position(report, wall, 5))
        for patch in ({"mode": 1}, {"mode": None}, {"lat": 91}, {"lon": float("nan")},
                      {"time": None}, {"time": (wall - timedelta(seconds=6)).isoformat()},
                      {"time": (wall + timedelta(seconds=3)).isoformat()}):
            with self.subTest(patch=patch):
                self.assertFalse(valid_position({**report, **patch}, wall, 5))

    def test_receiver_traffic_without_fix_is_not_a_fix(self):
        wall = datetime(2026, 10, 17, tzinfo=timezone.utc)
        health = ReceiverHealth(0)
        health.observe({"class": "TPV", "mode": 1}, 1, wall, 5)
        self.assertEqual(health.snapshot(1, wall, 5)["reason"], "no_position_fix")
        self.assertFalse(health.snapshot(1, wall, 5)["fix_valid"])

    def test_fix_loss_silence_and_recovery(self):
        wall = datetime(2026, 10, 17, tzinfo=timezone.utc)
        health = ReceiverHealth(0)
        report = {"class": "TPV", "mode": 3, "lat": -38, "lon": 145, "time": wall.isoformat()}
        health.observe(report, 1, wall, 5)
        self.assertTrue(health.snapshot(1, wall, 5)["fix_valid"])
        health.observe({"class": "SKY"}, 7, wall + timedelta(seconds=6), 5)
        self.assertFalse(health.snapshot(7, wall + timedelta(seconds=6), 5)["fix_valid"])
        self.assertEqual(health.last_tpv, 1)
        self.assertEqual(health.snapshot(13, wall + timedelta(seconds=12), 5)["state"], "stale")
        health.observe(report, 14, wall, 5)
        self.assertTrue(health.snapshot(14, wall, 5)["fix_valid"])
        health.observe({"class": "TPV", "mode": 1}, 15, wall, 5)
        self.assertFalse(health.snapshot(15, wall, 5)["fix_valid"])

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

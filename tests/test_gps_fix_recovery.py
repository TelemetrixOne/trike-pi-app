import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("gps", types.ModuleType("gps"))
from hpr_gateway.services.gps_recovery import (
    RecoveryPolicy, load_last_attempt, save_attempt, restart_gps, restore_budget,
)


class FixRecoveryTests(unittest.TestCase):
    def report(self, stamp):
        return {"class": "TPV", "mode": 3, "lat": -38, "lon": 145, "time": stamp.isoformat()}

    def test_loss_triggers_at_ten_seconds_then_fails_after_twenty(self):
        wall = datetime.now(timezone.utc)
        policy = RecoveryPolicy(0)
        policy.observe(self.report(wall), 1, wall)
        self.assertFalse(policy.tick(10.99))
        self.assertTrue(policy.tick(11))
        self.assertEqual(policy.state, "recovering")
        self.assertFalse(policy.tick(30.99))
        self.assertFalse(policy.tick(31))
        self.assertEqual(policy.state, "failed")
        self.assertFalse(policy.tick(70.99))
        self.assertTrue(policy.tick(71))

    def test_healthy_fixes_never_restart_and_reacquisition_cancels_failure(self):
        wall = datetime.now(timezone.utc)
        policy = RecoveryPolicy(0)
        for second in range(100):
            stamp = wall + timedelta(seconds=second)
            policy.observe(self.report(stamp), second, stamp)
            self.assertFalse(policy.tick(second))
        self.assertTrue(policy.tick(109))
        stamp = wall + timedelta(seconds=110)
        policy.observe(self.report(stamp), 110, stamp)
        self.assertEqual(policy.state, "healthy")
        self.assertFalse(policy.tick(120))  # still inside cooldown after relapse
        self.assertEqual(policy.state, "cooldown")

    def test_stale_invalid_and_repeated_tpv_do_not_hide_loss(self):
        wall = datetime.now(timezone.utc)
        policy = RecoveryPolicy(0)
        report = self.report(wall)
        policy.observe(report, 1, wall)
        policy.observe(report, 5, wall + timedelta(seconds=4))
        self.assertAlmostEqual(policy.last_fix, 1)
        policy.observe({**report, "mode": 1}, 8, wall + timedelta(seconds=7))
        self.assertTrue(policy.tick(11))
        policy.observe(self.report(wall + timedelta(seconds=9)), 12, wall + timedelta(seconds=11))
        self.assertEqual(policy.state, "recovering")  # buffered pre-restart fix

    def test_boot_grace_and_cooldown_survive_watchdog_restart(self):
        policy = RecoveryPolicy(0)
        self.assertFalse(policy.tick(59.99))
        self.assertTrue(policy.tick(60))
        restarted = RecoveryPolicy(100, startup_grace=10)
        self.assertFalse(restarted.tick(109))
        self.assertTrue(restarted.tick(110))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "last-attempt.json"
            save_attempt(path, 1000)
            self.assertEqual(load_last_attempt(path, 100, 1010), 90)
            self.assertEqual(load_last_attempt(path, 100, 900), 100)
            path.write_text("invalid")
            self.assertEqual(load_last_attempt(path, 100, 1010), 100)

    def test_only_gps_services_are_ever_restarted(self):
        with patch("hpr_gateway.services.gps_recovery.subprocess.run") as run:
            restart_gps()
        self.assertEqual(run.call_args.args[0],
                         ["systemctl", "--no-block", "restart", "gpsd.service", "hpr-gps.service"])

    def test_live_receiver_initial_no_fix_does_not_restart_for_an_hour(self):
        policy = RecoveryPolicy(0)
        wall = datetime.now(timezone.utc)
        attempts = 0
        for second in range(3600):
            policy.observe({'class': 'TPV', 'mode': 1}, second, wall + timedelta(seconds=second))
            attempts += policy.tick(second)
        self.assertEqual(attempts, 0)
        self.assertEqual(policy.state, 'no_fix')

    def test_one_quick_attempt_after_real_fix_loss_then_live_no_fix_left_alone(self):
        policy = RecoveryPolicy(0)
        wall = datetime.now(timezone.utc)
        policy.observe(self.report(wall), 0, wall)
        attempts = 0
        for second in range(1, 600):
            policy.observe({'class': 'TPV', 'mode': 1}, second, wall + timedelta(seconds=second))
            attempts += policy.tick(second)
        self.assertEqual(attempts, 1)
        self.assertEqual(policy.state, 'no_fix')

    def test_stalled_transport_budget_and_restarts_are_persistent(self):
        policy = RecoveryPolicy(0)
        self.assertEqual(sum(policy.tick(second) for second in range(600)), 3)
        self.assertEqual(policy.state, 'budget_exhausted')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'budget.json'
            save_attempt(path, 1000, policy)
            resumed = RecoveryPolicy(100)
            restore_budget(path, resumed, 1000)
            self.assertFalse(resumed.tick(160))
            self.assertEqual(resumed.state, 'budget_exhausted')

    def test_missing_satellites_sky_and_mqtt_have_no_role_in_loss_clock(self):
        wall = datetime.now(timezone.utc)
        policy = RecoveryPolicy(0)
        policy.observe(self.report(wall), 0, wall)
        for second in range(1, 10):
            policy.observe({"class": "SKY"}, second, wall + timedelta(seconds=second))
            self.assertFalse(policy.tick(second))
        self.assertTrue(policy.tick(10))


if __name__ == "__main__":
    unittest.main()

import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('battery_deployment', Path(__file__).parents[1]/'scripts/deploy-pi-battery-calibration.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


class BatteryDeploymentTests(unittest.TestCase):
    def evidence(self, phase):
        return dict(schema_version=1, trike_id='trike1', topic_root='hpr', enabled=True, uses_pi=True,
                    blocked=phase in {'active', 'scheduled'}, phase=phase,
                    server_time_utc=datetime.now(timezone.utc).isoformat())

    def check(self, payload, approved=False):
        deployment.approved_phase(payload, dict(trike_id='trike1', topic_root='hpr'), approved)

    def test_armed_race_needs_explicit_prestart_approval(self):
        with self.assertRaises(deployment.MaintenanceBlocked):
            self.check(self.evidence('scheduled'))
        self.check(self.evidence('scheduled'), True)

    def test_active_race_is_always_refused_even_with_operator_flag(self):
        for approved in (False, True):
            with self.assertRaises(deployment.MaintenanceBlocked):
                self.check(self.evidence('active'), approved)

    def test_idle_is_admitted_but_unknown_stale_or_wrong_identity_is_refused(self):
        self.check(self.evidence('ended'))
        for changes in [dict(phase='unknown'), dict(trike_id='trike2'),
                        dict(server_time_utc=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())]:
            with self.assertRaises(deployment.MaintenanceBlocked):
                self.check({**self.evidence('ended'), **changes}, True)

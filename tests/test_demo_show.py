import json
import time
import unittest
import uuid

from hpr_gateway.services.demo_show import DemoShow, eligible, moving_nav, parse_request


class DemoShowTests(unittest.TestCase):
    def test_requires_snapshot_but_allows_existing_light_and_drs_modes(self):
        state = {
            "horn/state": "OFF", "headlight/state": "OFF", "headlight/mode": "AUTO",
            "headlight/requested_state": "OFF",
            "aux_lights/state": "OFF", "aux_lights/requested_state": "OFF", "drs/state": "CLOSE",
            "drs/mode": "RIDER_CONTROL", "input/drs_switch/state": "OFF",
        }
        self.assertTrue(eligible(state))
        self.assertTrue(eligible(dict(state, **{
            "headlight/state": "ON", "headlight/mode": "ON",
            "aux_lights/state": "ON", "aux_lights/requested_state": "ON",
            "drs/state": "OPEN", "drs/mode": "OPEN", "input/drs_switch/state": "ON",
        })))
        for key in state:
            modified = dict(state)
            modified.pop(key)
            self.assertFalse(eligible(modified))
        self.assertFalse(eligible(dict(state, **{"horn/state": "ON"})))

    def test_fresh_gps_motion_vetoes_demo(self):
        now = time.monotonic()
        nav = {"speed_kmh": 0.2, "fix_mode": 3, "fix_age_ms": 300, "location_valid": True}
        self.assertFalse(moving_nav(nav, now - 1, now))
        self.assertTrue(moving_nav(dict(nav, speed_kmh=5), now, now))
        self.assertFalse(moving_nav(dict(nav, speed_kmh=5), now - 5, now))
        self.assertFalse(moving_nav(dict(nav, speed_kmh=5, fix_mode=1), now, now))
        self.assertFalse(moving_nav(dict(nav, speed_kmh=5, fix_age_ms=5000), now, now))
        self.assertFalse(moving_nav({}, now, now))

    def test_request_must_be_fresh_unretained_uuid(self):
        identifier = str(uuid.uuid4())
        raw = json.dumps({"action": "START", "request_id": identifier, "stationary_confirmed": True}).encode()
        self.assertEqual(parse_request(raw, False), identifier)
        with self.assertRaises(ValueError):
            parse_request(raw, True)
        with self.assertRaises(ValueError):
            parse_request(b"START", False)
        with self.assertRaises(ValueError):
            parse_request(b'{"action":"START","request_id":"bad"}', False)
        with self.assertRaises(ValueError):
            parse_request(json.dumps({"action": "START", "request_id": identifier, "stationary_confirmed": False}).encode(), False)

    def test_demo_restores_live_modes_and_requested_states(self):
        show = DemoShow.__new__(DemoShow)
        show.states = {
            "horn/state": "OFF", "headlight/state": "ON", "headlight/mode": "AUTO",
            "headlight/requested_state": "ON", "aux_lights/state": "ON",
            "aux_lights/requested_state": "ON", "drs/state": "CLOSE",
            "drs/mode": "CLOSED", "input/drs_switch/state": "ON",
        }
        show.nav = {}
        show.nav_at = 0.0
        show.initial_switch = ""
        statuses = []
        show.status = lambda state, request_id=None, reason=None: statuses.append((state, reason))
        show.wait_safe = lambda duration: None

        def send(name, value):
            if name == "horn":
                show.states["horn/state"] = value
            elif name == "aux_lights":
                show.states["aux_lights/state"] = value
                show.states["aux_lights/requested_state"] = value
            elif name == "headlight":
                show.states["headlight/mode"] = value
                if value != "AUTO":
                    show.states["headlight/state"] = value
                    show.states["headlight/requested_state"] = value
            elif name == "drs":
                show.states["drs/mode"] = value
                show.states["drs/state"] = "OPEN" if value == "OPEN" else "CLOSE"

        show.send = send
        show.play(str(uuid.uuid4()))
        self.assertEqual(statuses[-1], ("completed", None))
        self.assertEqual(show.states["horn/state"], "OFF")
        self.assertEqual(show.states["aux_lights/requested_state"], "ON")
        self.assertEqual(show.states["headlight/mode"], "AUTO")
        self.assertEqual(show.states["headlight/requested_state"], "ON")
        self.assertEqual(show.states["drs/mode"], "CLOSED")


if __name__ == "__main__":
    unittest.main()

import json
import time
import unittest
import uuid
from unittest.mock import patch

from hpr_gateway.services.demo_show import (
    DemoShow, SHOW_CUES, SHOW_END_SECONDS, SHOW_DURATION_SECONDS, eligible, parse_request,
)


class DemoShowTests(unittest.TestCase):
    def test_choreography_is_ordered_and_leaves_room_for_restoration(self):
        times = [when for when, _, _ in SHOW_CUES]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[0], 1.0)
        self.assertLess(times[-1], SHOW_END_SECONDS)
        self.assertEqual(SHOW_DURATION_SECONDS, 10)
        self.assertLess(SHOW_END_SECONDS, SHOW_DURATION_SECONDS)
        drs_times = [when for when, name, _ in SHOW_CUES if name == 'drs']
        self.assertTrue(all(b - a >= 2 for a, b in zip(drs_times, drs_times[1:])))
        self.assertEqual([value for _, name, value in SHOW_CUES if name == "drs"],
                         ["OPEN", "CLOSED", "OPEN", "CLOSED"])
        self.assertEqual([value for _, name, value in SHOW_CUES if name == "horn"].count("ON"), 8)

    def test_requires_snapshot_but_allows_existing_light_and_drs_modes(self):
        state = {
            "horn/state": "OFF", "headlight/state": "OFF", "headlight/mode": "AUTO",
            "headlight/requested_state": "OFF",
            "aux_lights/state": "OFF", "aux_lights/requested_state": "OFF", "drs/state": "CLOSE",
            "drs/mode": "RIDER_CONTROL", "input/drs_switch/state": "OFF",
            "input/headlamp/state": "OFF",
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

    def test_request_must_be_fresh_unretained_uuid(self):
        identifier = str(uuid.uuid4())
        issued = time.time()
        raw = json.dumps({'schema_version': 1, 'action': 'START', 'request_id': identifier,
                          'issued_at': issued, 'expires_at': issued + 5}).encode()
        self.assertEqual(parse_request(raw, False), identifier)
        with self.assertRaises(ValueError):
            parse_request(raw, True)
        with self.assertRaises(ValueError):
            parse_request(b"START", False)
        with self.assertRaises(ValueError):
            parse_request(b'{"action":"START","request_id":"bad"}', False)
        with self.assertRaises(ValueError):
            parse_request(json.dumps({"action": "START", "request_id": identifier, "extra": True}).encode(), False)

    def test_demo_restores_live_modes_and_requested_states(self):
        show = DemoShow.__new__(DemoShow)
        show.states = {
            "horn/state": "OFF", "headlight/state": "ON", "headlight/mode": "AUTO",
            "headlight/requested_state": "ON", "aux_lights/state": "ON",
            "aux_lights/requested_state": "ON", "drs/state": "CLOSE",
            "drs/mode": "CLOSED", "input/drs_switch/state": "ON",
            "input/headlamp/state": "ON",
        }
        statuses = []
        commands = []
        clock = [0.0]
        horn_times = []
        show.status = lambda state, request_id=None, reason=None: statuses.append((state, reason))

        def send(name, value):
            if name == 'horn':
                horn_times.append((clock[0], value))
            # Include the real configured DRS transition plus acknowledgement.
            clock[0] += 0.55 if name == 'drs' else 0.025
            commands.append((name, value))
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
                    show.states["input/headlamp/state"] = value
            elif name == "drs":
                show.states["drs/mode"] = value
                show.states["drs/state"] = "OPEN" if value == "OPEN" else "CLOSE"

        show.send = send
        show.wait_until = DemoShow.wait_until
        with patch('hpr_gateway.services.demo_show.time.monotonic', side_effect=lambda: clock[0]), \
                patch('hpr_gateway.services.demo_show.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            show.play(str(uuid.uuid4()))
        self.assertAlmostEqual(clock[0], 10.0)
        for (on_at, on), (off_at, off) in zip(horn_times[0:16:2], horn_times[1:16:2]):
            self.assertEqual((on, off), ('ON', 'OFF'))
            self.assertGreaterEqual(off_at - on_at, 0.17)
        self.assertEqual(commands[:2], [("aux_lights", "OFF"), ("headlight", "OFF")])
        self.assertEqual(commands.count(("drs", "OPEN")), 2)
        self.assertEqual(commands.count(("horn", "ON")), 8)
        self.assertEqual(commands.count(("headlight", "ON")), 6)  # five accents plus requested-state restoration
        self.assertEqual(statuses[-1], ("completed", None))
        self.assertEqual(show.states["horn/state"], "OFF")
        self.assertEqual(show.states["aux_lights/requested_state"], "ON")
        self.assertEqual(show.states["headlight/mode"], "AUTO")
        self.assertEqual(show.states["headlight/requested_state"], "ON")
        self.assertEqual(show.states["drs/mode"], "CLOSED")

    def test_no_show_if_headlamp_feedback_stays_on(self):
        show = DemoShow.__new__(DemoShow)
        show.states = {
            "horn/state": "OFF", "headlight/state": "ON", "headlight/mode": "ON",
            "headlight/requested_state": "ON", "aux_lights/state": "ON",
            "aux_lights/requested_state": "ON", "drs/state": "CLOSE",
            "drs/mode": "CLOSED", "input/drs_switch/state": "ON",
            "input/headlamp/state": "ON",
        }
        commands = []
        statuses = []
        show.status = lambda state, request_id=None, reason=None: statuses.append((state, reason))

        def send(name, value):
            commands.append((name, value))
            if name == "aux_lights":
                show.states["aux_lights/requested_state"] = value
                show.states["aux_lights/state"] = value
            elif name == "headlight":
                show.states["headlight/mode"] = value
                show.states["headlight/state"] = value
                show.states["headlight/requested_state"] = value
            elif name == "horn":
                show.states["horn/state"] = value
            elif name == "drs":
                show.states["drs/mode"] = value

        show.send = send
        show.wait_until = lambda _start, _seconds: None
        show.wait_state = lambda key, expected, timeout=2.0: (
            None if show.states.get(key) == expected else
            (_ for _ in ()).throw(RuntimeError(f"{key} did not acknowledge {expected}"))
        )
        show.play(str(uuid.uuid4()))
        self.assertEqual(statuses[-1][0], "failed")
        self.assertIn("input/headlamp/state", statuses[-1][1])
        self.assertNotIn(("horn", "ON"), commands)
        self.assertNotIn(("drs", "OPEN"), commands)
        self.assertEqual(show.states["headlight/mode"], "ON")


if __name__ == "__main__":
    unittest.main()

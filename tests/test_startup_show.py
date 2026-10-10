import unittest

from hpr_gateway.services.startup_show import eligible


class StartupShowTests(unittest.TestCase):
    def test_requires_idle_outputs_and_inputs(self):
        idle = {
            "horn/state": "OFF", "headlight/state": "OFF", "headlight/mode": "AUTO",
            "input/headlamp/state": "OFF", "aux_lights/state": "OFF",
            "input/aux_lights/state": "OFF",
        }
        self.assertTrue(eligible(idle, headlight=True, aux=True, horn=True))
        for key in idle:
            modified = dict(idle)
            modified.pop(key)
            self.assertFalse(eligible(modified, headlight=True, aux=True, horn=True))
        for key in ("horn/state", "headlight/state", "input/headlamp/state", "aux_lights/state", "input/aux_lights/state"):
            modified = dict(idle, **{key: "ON"})
            self.assertFalse(eligible(modified, headlight=True, aux=True, horn=True))

    def test_headlight_override_blocks_show(self):
        states = {"headlight/state": "OFF", "headlight/mode": "ON", "input/headlamp/state": "OFF"}
        self.assertFalse(eligible(states, headlight=True, aux=False, horn=False))


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "demo_trigger", Path(__file__).parents[1] / "bin/hpr-demo-sequence.py"
)
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)


class Published:
    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return True


class FakeClient:
    def __init__(self, state="completed"):
        self.state = state
        self.publications = []
        self.subscription = None
        self.disconnected = False

    def connect(self, host, port, keepalive):
        self.connection = host, port, keepalive

    def loop_start(self):
        self.on_connect(self, None, None, 0)

    def subscribe(self, topic, qos):
        self.subscription = topic, qos
        self.on_subscribe(self, None, 1)
        return 0, 1

    def publish(self, topic, payload, qos, retain):
        self.publications.append((topic, json.loads(payload), qos, retain))
        request_id = json.loads(payload)["request_id"]
        for state in ("running", self.state):
            status = {"state": state, "request_id": request_id}
            self.on_message(self, None, SimpleNamespace(
                topic=self.subscription[0], payload=json.dumps(status).encode()
            ))
        return Published()

    def loop_stop(self):
        pass

    def disconnect(self):
        self.disconnected = True


class DemoTriggerTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(topic_root="hpr", trike_id="trike1",
                                      mqtt=SimpleNamespace(host="broker", port=1883))

    def test_publishes_fresh_nonretained_request_and_waits_for_completion(self):
        client, lines = FakeClient(), []
        self.assertEqual(trigger.run_demo(self.config, client=client, output=lines.append), 0)
        topic, payload, qos, retain = client.publications[0]
        self.assertEqual(topic, "hpr/trike1/control/demo/request")
        self.assertEqual(payload["action"], "START")
        self.assertEqual(len(payload["request_id"]), 36)
        self.assertEqual((qos, retain), (1, False))
        self.assertEqual(client.subscription, ("hpr/trike1/control/demo/status", 1))
        self.assertTrue(client.disconnected)
        self.assertTrue(any("Demo completed" in line for line in lines))

    def test_rejection_returns_failure(self):
        self.assertEqual(trigger.run_demo(self.config, client=FakeClient("rejected"), output=lambda _: None), 1)

    def test_confirmation_decline_sends_nothing(self):
        with patch("builtins.input", return_value="NO"), patch.object(trigger, "load_config") as load:
            self.assertEqual(trigger.main([]), 1)
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()

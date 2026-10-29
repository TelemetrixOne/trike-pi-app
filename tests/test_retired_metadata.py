import unittest
from hpr_gateway.retired_metadata import strip_health_discovery


SOURCE = b'''import json
class HealthAgent:
    def publish_discovery(self):
        self.client.publish("retired/config", "metadata", retain=True)
    def check(self):
        return self.enabled
    def run_forever(self):
        self.publish_discovery()
        self.client.publish("native/state", self.check())
'''


class RetiredMetadataTests(unittest.TestCase):
    def test_only_metadata_method_and_startup_call_removed(self):
        result = strip_health_discovery(SOURCE)
        self.assertNotIn(b'publish_discovery', result)
        self.assertIn(b'return self.enabled', result)
        self.assertIn(b'self.client.publish("native/state", self.check())', result)

    def test_crlf_and_comments_preserved(self):
        source = SOURCE.replace(b'import json', b'import json # keep').replace(b'\n', b'\r\n')
        result = strip_health_discovery(source)
        self.assertIn(b'import json # keep\r\n', result)
        self.assertIn(b'return self.enabled\r\n', result)

    def test_missing_or_changed_structure_rejected(self):
        for source in (b'class Other: pass', SOURCE.replace(b'publish_discovery()', b'publish_discovery(1)'),
                       SOURCE.replace(b'self.publish_discovery()', b'pass')):
            with self.subTest(source=source):
                with self.assertRaises((ValueError, SyntaxError)):
                    strip_health_discovery(source)

    def test_repeat_application_rejected(self):
        with self.assertRaises(ValueError):
            strip_health_discovery(strip_health_discovery(SOURCE))


if __name__ == '__main__': unittest.main()

import json
from pathlib import Path
import tempfile
import unittest
import uuid
from hpr_gateway.demo_protocol import DemoJournal, envelope


class DemoProtocolTests(unittest.TestCase):
    def payload(self, **changes):
        data = {'schema_version': 1, 'action': 'START', 'request_id': str(uuid.uuid4()),
                'issued_at': 1000, 'expires_at': 1005}
        data.update(changes)
        return json.dumps(data).encode()

    def test_expired_retained_unsynchronised_and_legacy_requests_rejected(self):
        raw = self.payload()
        self.assertEqual(envelope(raw, now=1001)['action'], 'START')
        for kwargs in ({'retained': True}, {'clock_ready': False}, {'now': 1005}):
            options = {'now': 1001}; options.update(kwargs)
            with self.assertRaises(ValueError): envelope(raw, **options)
        for data in (self.payload(issued_at=True), self.payload(expires_at=1006), self.payload(issued_at=1004),
                     self.payload(expires_at=float('nan')), b'{"action":"START","request_id":"bad"}', b'[]'):
            with self.assertRaises(ValueError): envelope(data, now=1001)

    def test_duplicate_is_consumed_across_observer_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.db'; identity = str(uuid.uuid4())
            self.assertTrue(DemoJournal(path).claim(identity, 1000))
            self.assertFalse(DemoJournal(path).claim(identity, 1001))
            self.assertTrue(DemoJournal(path).claim(str(uuid.uuid4()), 1002))


if __name__ == '__main__': unittest.main()

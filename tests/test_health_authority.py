import json
from pathlib import Path
import tempfile
import unittest
from hpr_gateway.services.services_health import configured_tpms_selection, sensor_topics
from hpr_gateway.video_health import VideoEvidence
from test_native_mqtt_only import config


class HealthAuthorityTests(unittest.TestCase):
    def test_tpms_uses_applied_positions_without_legacy_message_and_follows_changes(self):
        c = config()
        c.data['tpms'] = {'sensors': {name: {'enabled': True, 'position': position}
            for name, position in [('tpms4', 'front_left'), ('tpms2', 'front_right'), ('tpms6', 'rear'), ('tpms1', 'spare')]}}
        assignment, names, error = configured_tpms_selection(c)
        self.assertIsNone(error)
        self.assertEqual(names, ['tpms4', 'tpms2', 'tpms6'])
        self.assertEqual(assignment['rear'], 'tpms6')
        c.data['tpms']['sensors']['tpms6']['position'] = 'spare'
        c.data['tpms']['sensors']['tpms1']['position'] = 'rear'
        self.assertEqual(configured_tpms_selection(c)[1], ['tpms4', 'tpms2', 'tpms1'])
        c.data['tpms']['sensors']['tpms6']['position'] = 'rear'
        self.assertIsNotNone(configured_tpms_selection(c)[2])

    def test_health_sensor_sources_use_configured_root(self):
        c = config()
        c.data['tpms'] = {'sensors': {'tpms1': {'enabled': True}}}
        c.data['power_cadence'] = {'devices': {'pedal': {'enabled': True}}}
        tpms, pedals, data = sensor_topics(c)
        self.assertEqual(tpms['tpms1'], 'custom-root/trike1/tpms/tpms1/availability')
        self.assertTrue(all(t.startswith('custom-root/trike1/') for t in pedals + data))

    def test_process_alive_is_not_enough_for_video_health(self):
        with tempfile.TemporaryDirectory() as folder:
            c = config(); path = Path(folder) / 'status.json'
            c.data['services']['video'] = {'enabled': True}
            c.data['video'] = {'runtime_status_path': str(path), 'cameras': {'front': {'enabled': True}}}
            e = VideoEvidence()
            self.assertEqual(e.inspect(c, 'Good', 100)['overall'], 'Failed')
            document = {'display_mode': None, 'children': {
                'front-capture': {'pid': 1, 'at': 100, 'frames': 30},
                'front-publish': {'pid': 2, 'at': 100, 'frames': 30}}}
            path.write_text(json.dumps(document))
            good = e.inspect(c, 'Good', 100)
            self.assertEqual(good['overall'], 'Good')
            self.assertFalse(good['display_connected'])
            for item in document['children'].values(): item['at'] = 111
            path.write_text(json.dumps(document))
            self.assertEqual(e.inspect(c, 'Good', 111)['overall'], 'Failed')
            document['children']['front-publish']['frames'] = 60
            document['children']['front-capture']['frames'] = 60
            path.write_text(json.dumps(document))
            self.assertEqual(e.inspect(c, 'Good', 111)['overall'], 'Good')
            document['display_mode'] = '1280x720'
            path.write_text(json.dumps(document))
            self.assertEqual(e.inspect(c, 'Good', 111)['overall'], 'Failed')


if __name__ == '__main__': unittest.main()

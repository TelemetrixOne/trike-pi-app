import copy
import unittest
from hpr_gateway.config import HprConfig
from hpr_gateway.media_broker import render
from test_native_mqtt_only import config


class MediaBrokerPolicyTests(unittest.TestCase):
    def setup_config(self):
        cfg = config()
        cfg.data['network']['mediamtx'] = {'tailscale_host': '100.64.1.2', 'trusted_reader_hosts': ['100.64.1.2', '100.64.1.3']}
        cfg.data['video'] = {'cameras': {'front': {'enabled': True, 'path': 'front'}, 'rear': {'enabled': True, 'path': 'rear'}}}
        return cfg

    def test_remote_peers_read_only_local_publish_only_and_no_implicit_services(self):
        cfg = self.setup_config(); original = copy.deepcopy(cfg.data)
        data = render(cfg)
        local, remote = data['authInternalUsers']
        self.assertEqual(local['ips'], ['127.0.0.1/32', '::1/128'])
        self.assertEqual(remote['ips'], ['100.64.1.2/32', '100.64.1.3/32'])
        self.assertEqual(remote['permissions'], [{'action': 'read', 'path': p} for p in ('front','rear')])
        self.assertEqual({p['action'] for p in local['permissions']}, {'publish','read'})
        self.assertEqual(data['rtspTransports'], ['tcp'])
        for key in ('moq','hls','webrtc','rtmp','srt','metrics','api','pprof','playback'): self.assertIs(data[key], False)
        self.assertEqual(cfg.data, original)

    def test_no_broad_or_missing_primary_trust(self):
        for hosts in (['0.0.0.0'], ['::'], ['224.0.0.1'], ['127.0.0.1'], ['100.64.1.0/24'], ['100.64.1.3']):
            cfg = self.setup_config(); cfg.data['network']['mediamtx']['trusted_reader_hosts'] = hosts
            with self.assertRaises(ValueError): render(cfg)

    def test_no_catchall_duplicate_or_regex_camera_paths(self):
        for path in ('all_others', '~.*', 'front'):
            cfg = self.setup_config(); cfg.data['video']['cameras']['rear']['path'] = path
            with self.assertRaises(ValueError): render(cfg)

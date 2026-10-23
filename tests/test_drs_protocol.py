import json
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hpr_gateway.drs_protocol import DRSProtocol, DRSClock
from hpr_gateway.services import gpio_control as gpio


class DRSProtocolTests(unittest.TestCase):
    def envelope(self, **changes):
        command = {'schema_version': 1, 'command_id': str(uuid.uuid4()),
                   'mode': 'OPEN', 'issued_at': 1000, 'expires_at': 1005}
        command.update(changes)
        return json.dumps(command).encode()

    def parse(self, protocol, payload, retained=False, ready=True, now=1001):
        with patch('hpr_gateway.drs_protocol.time.time', return_value=now), \
             patch('hpr_gateway.drs_protocol.time.monotonic', return_value=100):
            return protocol.parse(payload, retained, ready)

    def test_accepts_all_modes_and_rejects_unsafe_envelopes(self):
        for mode in ('OPEN', 'CLOSED', 'RIDER_CONTROL'):
            self.assertIsNone(self.parse(DRSProtocol(), self.envelope(mode=mode))[1])
        for changes in ({'mode': 'CLOSE'}, {'issued_at': True}, {'issued_at': float('nan')},
                        {'expires_at': float('inf')}, {'issued_at': 10 ** 400}, {'schema_version': True},
                        {'expires_at': 1006}, {'expires_at': 1000},
                        {'command_id': 'not-a-uuid'}, {'issued_at': 1004, 'expires_at': 1005}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.parse(DRSProtocol(), self.envelope(**changes))[1])
        for raw in (b'[]', b'not-json', b'x' * 2049, b'[' * 1000 + b']' * 1000,
                    b'{"mode":"OPEN","mode":"CLOSED"}'):
            self.assertEqual(self.parse(DRSProtocol(), raw)[1]['result'], 'rejected')
        self.assertEqual(self.parse(DRSProtocol(), self.envelope(), retained=True)[1]['reason'], 'retained_command')
        self.assertEqual(self.parse(DRSProtocol(), self.envelope(), ready=False)[1]['reason'], 'clock_unsynchronised')
        self.assertEqual(self.parse(DRSProtocol(), self.envelope(), now=1005)[1]['result'], 'expired')

    def test_duplicate_and_capacity_never_evict_live_identities(self):
        p = DRSProtocol(capacity=1)
        payload = self.envelope()
        command, error = self.parse(p, payload)
        self.assertIsNone(error)
        with patch('hpr_gateway.drs_protocol.time.monotonic', return_value=100):
            p.remember(command['command_id'], 'accepted')
        self.assertEqual(self.parse(p, payload)[1]['reason'], 'already_accepted')
        self.assertEqual(self.parse(p, self.envelope())[1]['reason'], 'command_cache_full')
        self.assertEqual(len(p.seen), 1)
        self.assertEqual(self.parse(p, payload)[1]['result'], 'duplicate')

    def test_expired_cache_cannot_revive_expired_command(self):
        p = DRSProtocol(); payload = self.envelope()
        command, _ = self.parse(p, payload)
        with patch('hpr_gateway.drs_protocol.time.monotonic', return_value=0):
            p.remember(command['command_id'], 'accepted')
        self.assertEqual(self.parse(p, payload, now=1100)[1]['result'], 'expired')

    def test_events_have_instance_identity_sequence_and_no_position_claim(self):
        p = DRSProtocol()
        a, b = p.event('CLOSED', 'CLOSE', 'moving'), p.event('CLOSED', 'CLOSE', 'released')
        self.assertEqual(a['target'], 'CLOSED')
        self.assertFalse(a['position_verified'])
        self.assertEqual(b['seq'], a['seq'] + 1)
        self.assertNotEqual(p.boot_id, DRSProtocol().boot_id)
        self.assertFalse(DRSClock().ready)


class DRSActuationTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(gpio.GPIOControlService)
        self.service._drs_lock = threading.RLock()
        self.output = Mock()
        self.service._drs = gpio.DRSRuntime(12, 333, 556, 1444, output=self.output,
                                           current_pulse_us=556)
        self.service._drs_protocol = DRSProtocol()
        self.service._drs_clock = SimpleNamespace(ready=True)
        self.service._sense_inputs = {}
        self.service._publish = Mock()
        self.settings = {'trike': 'trike1', 'transition_seconds': 0.5, 'switch_behavior': 'pulse_release',
                         'pulse_release_seconds': 0.5, 'rider_control_input': 'drs_switch'}
        self.patches = [patch.object(gpio, 'DRS', self.settings),
                        patch.object(gpio, 'MQTT', {'topic_root': 'hpr'})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def command(self, mode='OPEN', ident=None, now=100, retained=False):
        ident = ident or str(uuid.uuid4())
        payload = json.dumps({'schema_version': 1, 'command_id': ident, 'mode': mode,
                              'issued_at': 1000, 'expires_at': 1005}).encode()
        with patch.object(gpio.time, 'monotonic', return_value=now), \
             patch.object(gpio.time, 'time', return_value=1001):
            self.service._handle_drs_command(SimpleNamespace(payload=payload, retain=retained))
        acks = [json.loads(c.args[1]) for c in self.service._publish.call_args_list
                if c.args[0].endswith('/ack')]
        return ident, acks[-1]

    def tick(self, now):
        with patch.object(gpio.time, 'monotonic', return_value=now):
            self.service._release_drs_if_due()

    def test_correlated_ack_target_not_actual_position_and_lifecycle(self):
        ident, ack = self.command()
        self.assertEqual(ack['result'], 'accepted')
        self.assertEqual(ack['command_id'], ident)
        self.assertEqual(ack['last_command_id'], ident)
        self.assertEqual(ack['phase'], 'moving')
        self.assertFalse(ack['position_verified'])
        self.assertEqual(self.service._drs.current_pulse_us, 556)
        self.tick(100.5)
        self.assertEqual(self.service._drs_phase(), 'holding')
        self.tick(101)
        self.assertEqual(self.service._drs_phase(), 'released')
        self.assertIsNone(self.service._drs.output)
        seqs = [json.loads(c.args[1])['seq'] for c in self.service._publish.call_args_list
                if c.args[0].endswith(('/ack', '/status'))]
        self.assertEqual(seqs, sorted(set(seqs)))

    def test_new_identity_retry_repulses_but_duplicate_never_does(self):
        ident, _ = self.command()
        self.tick(100.5); self.tick(101)
        with patch.object(gpio, 'PWMOutputDevice') as factory:
            _, duplicate = self.command(ident=ident, now=101.1)
            self.assertEqual(duplicate['result'], 'duplicate')
            factory.assert_not_called()
            _, retry = self.command(now=101.2)
            self.assertEqual(retry['result'], 'accepted')
            factory.assert_called_once_with(12, frequency=333, initial_value=0)
            self.assertAlmostEqual(factory.return_value.value, 1444 * 333 / 1000000)

    def test_busy_duplicate_does_not_extend_movement_or_release(self):
        ident, _ = self.command()
        start = self.service._drs.transition_started_monotonic
        _, busy = self.command(now=100.1)
        self.assertEqual(busy['result'], 'busy')
        self.assertEqual(self.service._drs.transition_started_monotonic, start)
        self.tick(100.5)
        release = self.service._drs.pulse_release_at_monotonic
        self.command(ident=ident, now=100.8)
        self.assertEqual(self.service._drs.pulse_release_at_monotonic, release)

    def test_same_target_retry_has_cooldown(self):
        self.service._drs.state = 'OPEN'
        self.service._drs.current_pulse_us = 1444
        self.service._drs.output = None
        self.service._drs.last_actuation_monotonic = 100
        _, ack = self.command(now=100.2)
        self.assertEqual(ack['reason'], 'retry_cooldown')

    def test_opposite_target_reversal_remains_allowed(self):
        self.command()
        self.tick(100.25)
        _, ack = self.command('CLOSED', now=100.3)
        self.assertEqual(ack['result'], 'accepted')
        self.assertEqual(self.service._drs.transition_target_pulse_us, 556)

    def test_rider_control_resolves_only_designated_physical_input(self):
        sensor = object()
        self.service._sense_inputs['trike1:drs_switch'] = sensor
        with patch.object(self.service, '_sense_input_state', return_value='ON') as read:
            ident, ack = self.command('RIDER_CONTROL')
            read.assert_called_with(sensor)
        self.assertEqual(ack['mode'], 'RIDER_CONTROL')
        self.assertEqual(ack['target'], 'OPEN')
        self.assertEqual(ack['source'], 'LOCAL')
        self.assertEqual(ack['last_command_id'], ident)

    def test_legacy_override_clears_correlation_without_changing_protocol(self):
        self.command()
        self.service._handle_message(SimpleNamespace(
            topic='hpr/trike1/control/drs/set', payload=b'CLOSE', retain=False))
        self.assertIsNone(self.service._drs_protocol.last_command_id)
        self.assertEqual(self.service._drs.mode, 'CLOSED')
        self.assertEqual(self.service._drs.state, 'CLOSE')
        before = self.service._drs.mode
        self.service._handle_message(SimpleNamespace(
            topic='hpr/trike1/control/drs/set', payload=b'OPEN', retain=True))
        self.assertEqual(self.service._drs.mode, before)

    def test_hardware_failure_reports_error_without_crashing_shared_owner(self):
        self.service._drs.output = None
        with patch.object(gpio, 'PWMOutputDevice', side_effect=RuntimeError('driver unavailable')):
            _, ack = self.command()
        self.assertEqual(ack['result'], 'rejected')
        self.assertEqual(ack['phase'], 'error')
        self.assertFalse(ack['position_verified'])
        self.assertIsNone(self.service._drs.transition_target_pulse_us)

    def test_retained_expired_and_unsynchronised_commands_never_actuate(self):
        with patch.object(self.service, '_set_drs_mode') as actuator:
            self.command(retained=True)
            self.service._drs_clock.ready = False
            self.command()
            actuator.assert_not_called()

    def test_heartbeat_is_bounded_and_never_reactuates(self):
        with patch.object(self.service, '_set_drs') as actuator:
            for now in (100, 100.02, 100.04, 104.99, 105):
                with patch.object(gpio.time, 'monotonic', return_value=now):
                    self.service._publish_drs_status()
            actuator.assert_not_called()
        statuses = [c for c in self.service._publish.call_args_list if c.args[0].endswith('/status')]
        self.assertEqual(len(statuses), 2)
        self.assertTrue(all(c.kwargs == {'retain': True, 'qos': 1} for c in statuses))

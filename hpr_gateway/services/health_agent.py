"""Canonical resource/process observer. Recovery belongs to dedicated owners."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone

from hpr_gateway.config import config_arg_parser, load_config
from hpr_gateway.mqtt import make_client
from hpr_gateway.runtime_health import disk_usage, memory_usage
from hpr_gateway.services.services_health import unit_expected

LOG = logging.getLogger('hpr_health_agent')


class HealthAgent:
    def __init__(self, config, client=None, state_path=None):
        self.config = config
        self.root = config.topic('agents', 'health')
        self.path = Path(state_path or os.path.join(os.getenv('STATE_DIRECTORY', '/var/lib/hpr-health-agent'), 'mode.json'))
        try:
            value = json.loads(self.path.read_text())
            self.enabled = value.get('enabled') is True
        except (OSError, ValueError, AttributeError):
            self.enabled = False
        self.stopping = threading.Event()
        self.client = client or make_client(config, f'hpr-{config.trike_id}-health-agent')
        self.client.max_queued_messages_set(4)
        self.client.username_pw_set(config.mqtt.username, config.mqtt.password)
        self.client.will_set(self.root + '/availability', 'offline', qos=1, retain=True)
        self.client.on_connect, self.client.on_message = self.on_connect, self.on_message

    def on_connect(self, client, _userdata, _flags, reason, _properties=None):
        if reason != 0:
            return
        client.subscribe(self.root + '/enabled/set', qos=1)
        client.publish(self.root + '/availability', 'online', qos=1, retain=True)
        client.publish(self.root + '/enabled/state', 'ON' if self.enabled else 'OFF', qos=1, retain=True)

    def on_message(self, client, _userdata, message):
        if message.topic != self.root + '/enabled/set':
            return
        mode = message.payload.decode(errors='replace').strip().upper()
        if mode not in ('ON', 'OFF'):
            return
        self.enabled = mode == 'ON'
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            pending = self.path.with_suffix('.tmp')
            with pending.open('w') as handle:
                json.dump({'enabled': self.enabled}, handle); handle.flush(); os.fsync(handle.fileno())
            os.chmod(pending, 0o600)
            pending.replace(self.path)
        except OSError:
            LOG.exception('Observer mode persistence failed')
        client.publish(self.root + '/enabled/state', mode, qos=1, retain=True)

    @staticmethod
    def service_state(unit):
        try:
            result = subprocess.run(['systemctl', 'is-active', unit], text=True, capture_output=True, timeout=3)
            state = result.stdout.strip() or 'unknown'
            return {'unit': unit, 'active': result.returncode == 0 and state == 'active', 'state': state, 'error': None}
        except (OSError, subprocess.SubprocessError) as exc:
            return {'unit': unit, 'active': False, 'state': 'unknown', 'error': type(exc).__name__}

    def metrics(self):
        result = {'cpu_temperature_c': None, 'load_1m': os.getloadavg()[0] if hasattr(os, 'getloadavg') else None,
                  'memory_used_pct': None, 'disk_used_pct': None, 'throttled_raw': None}
        try: result['cpu_temperature_c'] = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
        except (OSError, ValueError): pass
        try: result['memory_used_pct'] = memory_usage(Path('/proc/meminfo').read_text())
        except (OSError, ValueError, KeyError): pass
        try: result['disk_used_pct'] = disk_usage()
        except (OSError, ValueError): pass
        try:
            value = subprocess.run(['vcgencmd', 'get_throttled'], text=True, capture_output=True, timeout=2)
            result['throttled_raw'] = value.stdout.strip().partition('=')[2] or None
        except (OSError, subprocess.SubprocessError): pass
        return result

    def check(self):
        units = self.config.get('health_agent.units') or list(self.config.get('services.services_health.units', {}).values())
        services = [self.service_state(unit) for unit in dict.fromkeys(units) if unit_expected(self.config, unit)]
        metrics = self.metrics()
        limits = self.config.get('health_agent.limits', {})
        alerts = [{'severity': 'critical', 'code': 'service_' + value['unit'],
                   'message': value['unit'] + ' is ' + value['state']} for value in services if not value['active']]
        temperature = metrics['cpu_temperature_c']
        if temperature is not None and temperature >= limits.get('temperature_warning_c', 75):
            alerts.append({'severity': 'critical' if temperature >= limits.get('temperature_critical_c', 82) else 'warning',
                           'code': 'cpu_hot', 'message': 'Inspect Pi CPU temperature/cooling'})
        if metrics['disk_used_pct'] is not None and metrics['disk_used_pct'] >= limits.get('disk_warning_pct', 90):
            alerts.append({'severity': 'warning', 'code': 'disk_full', 'message': 'Pi root filesystem nearly full'})
        if metrics['throttled_raw'] not in (None, '0x0'):
            alerts.append({'severity': 'critical', 'code': 'throttled', 'message': 'Pi reports power/thermal throttling'})
        observed = 'critical' if any(a['severity'] == 'critical' for a in alerts) else 'warning' if alerts else 'ok'
        return {'schema': 'hpr.health_agent.v1', 'trike_id': self.config.trike_id,
                'generated_at': datetime.now(timezone.utc).isoformat(), 'enabled': self.enabled,
                'status': observed if self.enabled else 'disabled', 'observed_status': observed,
                'summary': alerts[0]['message'] if alerts and self.enabled else
                    'Process/resource observer only; see separate device/frame health',
                'metrics': metrics, 'services': services, 'alerts': alerts,
                'remediations': [], 'last_remediation': None, 'remediation_owner': 'systemd_and_dedicated_recovery',
                'recovery_ownership': {'process_exit': 'systemd', 'gps_transport': 'hpr-gps-recovery',
                                       'hrm_scanner': 'hpr-bluetooth-recovery'},
                'scope': 'process_resource_observation_not_device_readiness'}

    def run(self):
        self.client.connect_async(self.config.mqtt.host, self.config.mqtt.port, 30)
        self.client.loop_start()
        try:
            while not self.stopping.is_set():
                self.client.publish(self.root + '/state', json.dumps(self.check()), qos=1, retain=True)
                self.stopping.wait(max(5, float(self.config.get('health_agent.interval_seconds', 60))))
        finally:
            self.client.publish(self.root + '/availability', 'offline', qos=1, retain=True)
            self.client.disconnect(); self.client.loop_stop()


def main():
    args = config_arg_parser('Canonical non-actuating Pi health observer').parse_args()
    config = load_config(args.config)
    if not config.enabled('health_agent'):
        return
    agent = HealthAgent(config)
    signal.signal(signal.SIGTERM, lambda *_: agent.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stopping.set())
    agent.run()


if __name__ == '__main__': main()

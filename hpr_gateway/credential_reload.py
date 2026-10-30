"""Explicit credential-only MQTT reload; no GPIO lifecycle or commands."""
import copy
import logging
import threading

from hpr_gateway.config import load_config

LOG = logging.getLogger(__name__)


class CredentialReload:
    def __init__(self, client, config):
        self.client = client
        self.path = config.path
        self.identity = (config.trike_id, config.topic_root, config.mqtt.host, config.mqtt.port)
        self.gpio = copy.deepcopy(config.get('gpio'))
        self.credentials = (config.mqtt.username, config.mqtt.password)
        self.requested = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name='mqtt-credential-reload', daemon=True)

    def apply(self, config):
        if (config.trike_id, config.topic_root, config.mqtt.host, config.mqtt.port) != self.identity or config.get('gpio') != self.gpio:
            raise ValueError('Credential reload refuses broker, identity or GPIO configuration changes')
        credentials = (config.mqtt.username, config.mqtt.password)
        if credentials == self.credentials:
            return False
        # Only the network loop stops. The GPIO controller continues polling,
        # preserving output objects, requested modes and DRS command identity.
        self.client.disconnect()
        self.client.loop_stop()
        self.client.username_pw_set(*credentials)
        self.credentials = credentials
        if not self.stopping.is_set():
            self.client.connect_async(self.identity[2], self.identity[3], keepalive=60)
            self.client.loop_start()
        LOG.info('MQTT credential reload initiated; controller state preserved')
        return True

    def run(self):
        while not self.stopping.is_set():
            if not self.requested.wait(.5):
                continue
            self.requested.clear()
            if self.stopping.is_set():
                break
            try:
                self.apply(load_config(self.path))
            except Exception as exc:
                # Config errors can contain secret input: log only the class.
                LOG.error('Credential-only reload failed: %s', type(exc).__name__)

    def stop(self):
        self.stopping.set()
        self.requested.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            LOG.error('Credential network worker did not stop within deadline')

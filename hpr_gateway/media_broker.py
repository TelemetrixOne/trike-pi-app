"""Deployment-owned RTSP peer policy; camera/encoder settings are untouched."""
import ipaddress
import re


def trusted_readers(config):
    primary = config.get('network.mediamtx.tailscale_host')
    values = config.get('network.mediamtx.trusted_reader_hosts') or [primary]
    if not isinstance(values, list) or not values:
        raise ValueError('Enrolled deployment-owned trusted reader IPs required')
    hosts = []
    for value in values:
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError('Trusted readers must be specific remote unicast IP addresses')
        if str(address) not in hosts: hosts.append(str(address))
    if str(ipaddress.ip_address(primary)) not in hosts:
        raise ValueError('The configured central tailnet host must remain a trusted reader')
    return hosts


def render(config, listen_address=None):
    paths = {}
    for role, camera in config.get('video.cameras', {}).items():
        if not camera.get('enabled'): continue
        path = camera.get('path') or role
        if not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]*', path) or path in ('all', 'all_others') or path in paths:
            raise ValueError('Broker requires unique explicit camera paths')
        paths[path] = {'source': 'publisher'}
    if not paths: raise ValueError('No enabled camera publisher paths')
    hosts = trusted_readers(config)
    return {'logLevel': 'info', 'logDestinations': ['stdout'], 'rtsp': True,
        'rtspAddress': listen_address or ':' + str(int(config.get('video.rtsp_port', 8554))),
        'rtspTransports': ['tcp'], 'rtmp': False, 'hls': False, 'webrtc': False,
        'srt': False, 'moq': False, 'api': False, 'metrics': False, 'pprof': False, 'playback': False,
        'authMethod': 'internal', 'authInternalUsers': [
            {'user': 'any', 'pass': '', 'ips': ['127.0.0.1/32', '::1/128'],
             'permissions': [{'action': action, 'path': path} for action in ('publish', 'read') for path in paths]},
            {'user': 'any', 'pass': '', 'ips': [str(ipaddress.ip_network(host, strict=False)) for host in hosts],
             'permissions': [{'action': 'read', 'path': path} for path in paths]}],
        'paths': paths}

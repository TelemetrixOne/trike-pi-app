#!/usr/bin/env python3
"""Guarded broker-only rollout; preserve native capture, HDMI and GPIO processes."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import yaml
from hpr_gateway.config import HprConfig, load_config
from hpr_gateway.config_transaction import config_lock, PENDING
from hpr_gateway.media_broker import render, trusted_readers
from hpr_gateway.services.trike_config_sync import ProfileApplier, atomic_write

ROOT = Path('/opt/hpr/gateway')
CONFIG = Path('/etc/hpr/hpr.yaml')
RUNTIME = ROOT / 'runtime/video'
UNIT = 'hpr-video-mediamtx.service'
VIEWER_UNIT = Path('/etc/systemd/system/hpr-video-compositor.service')
OLD_BINARY = '31f021d518e4da59295be8e7170f09257cc9be04981001da9c2cb9bea30b26e1'
OLD_UNIT = 'f90427f1aea89d17e8041820f649d207832def5d9f42a1e9fdc938a865852b02'
PROTECTED = ['hpr-video-compositor.service', 'hpr-gpio-control.service', 'hpr-heart-rate.service',
             'hpr-gps.service', 'hpr-tpms.service', 'hpr-power-cadence.service', 'hpr-trike-config-sync.service']


def run(*args, timeout=35):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints():
    return run('systemctl', 'show', *PROTECTED, '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState')


def capture_ids():
    value = json.loads(Path('/run/hpr-video/video-status.json').read_text())
    return {name: child['pid'] for name, child in value.get('children', {}).items()
            if name.endswith('-capture') or name == 'display'}


def peers():
    values = set()
    for line in run('ss', '-Htn', 'sport = :8554').splitlines():
        ip = ipaddress.ip_address(line.split()[4].rsplit(':', 1)[0].strip('[]'))
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped: ip = ip.ipv4_mapped
        if not ip.is_loopback: values.add(str(ip))
    return values


def decodes(config):
    result = {}
    for name, camera in config.get('video.cameras', {}).items():
        if not camera.get('enabled'): continue
        url = 'rtsp://127.0.0.1:8554/' + (camera.get('path') or name)
        info = json.loads(run('ffprobe', '-v', 'error', '-rtsp_transport', 'tcp',
            '-show_entries', 'stream=codec_name,width,height,avg_frame_rate', '-of', 'json', url, timeout=12))
        run('nice', '-n', '15', 'ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-rtsp_transport', 'tcp',
            '-i', url, '-map', '0:v:0', '-frames:v', '3', '-an', '-sn', '-f', 'null', '-', timeout=12)
        result[name] = info['streams']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--expected-broker-config-sha256', required=True)
    parser.add_argument('--trusted-reader', action='append', required=True)
    parser.add_argument('--broker-rollout-approved', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.broker_rollout_approved:
        raise RuntimeError('Root and approved brief broker rollout required')
    raw = CONFIG.read_bytes()
    if digest(CONFIG) != args.expected_config_sha256 or digest(RUNTIME / 'mediamtx.yml') != args.expected_broker_config_sha256:
        raise RuntimeError('Runtime configuration changed; repeat read-only comparison')
    if any(CONFIG.with_name(p).exists() for p in PENDING): raise RuntimeError('Pending central transaction')
    if digest(RUNTIME / 'mediamtx') != OLD_BINARY or digest(VIEWER_UNIT) != OLD_UNIT:
        raise RuntimeError('Broker/unit baseline changed; preserve unidentified enhancement')
    metadata = json.loads((SOURCE / 'vendor/mediamtx/build-provenance.json').read_text())
    binary = SOURCE / 'vendor/mediamtx/linux_arm64/mediamtx'
    if digest(binary) != metadata['binary_sha256'] or metadata['govulncheck_exit_code'] != 0:
        raise RuntimeError('Audited candidate provenance mismatch')
    os.chmod(binary, 0o755)
    config = load_config(str(CONFIG)); data = copy.deepcopy(config.data)
    prior_readers = data['network']['mediamtx'].get('trusted_reader_hosts')
    if prior_readers and prior_readers != args.trusted_reader: raise RuntimeError('Existing reader policy differs')
    data['network']['mediamtx']['trusted_reader_hosts'] = args.trusted_reader
    data['video'].update(mediamtx_version=metadata['version_label'], mediamtx_sha256=metadata['binary_sha256'].upper())
    updated = HprConfig(data, str(CONFIG)); allowed = set(trusted_readers(updated))
    if not peers() <= allowed: raise RuntimeError('An unidentified active reader needs classification')
    spec = importlib.util.spec_from_file_location('single_owner', SOURCE / 'scripts/update-pi-config-single-writer.py')
    owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)
    desired = owner.read_topic(config, 'desired', lambda value, _: value.get('hash') == config.get('configuration.trike_hash'))
    _, changed = ProfileApplier(str(CONFIG)).prepare(updated, desired)
    if changed: raise RuntimeError('Deployment metadata would change managed hardware services')
    broker_data = yaml.safe_dump(render(updated), sort_keys=False).encode()
    test_file = SOURCE / 'validated-production-broker.yaml'; test_file.write_bytes(broker_data)
    run(str(binary), '--validate-conf', str(test_file))
    before, captures, streams = fingerprints(), capture_ids(), decodes(config)
    controls = {str(p): digest(p) for p in (Path('/etc/hpr/camera-controls.yaml'), Path('/etc/hpr/video-local.yaml'))}
    sources = [p for p in (SOURCE / 'vendor/mediamtx').rglob('*') if p.is_file()]
    sources.append(SOURCE / 'hpr_gateway/media_broker.py')
    targets = [(p, ROOT / p.relative_to(SOURCE)) for p in sources]
    vendor_binary = ROOT / 'vendor/mediamtx/linux_arm64/mediamtx'
    for source, target in targets:
        if target.is_symlink(): raise RuntimeError('Unidentified live symlink: ' + str(target))
        if target.exists() and target != vendor_binary and digest(target) != digest(source):
            raise RuntimeError('Unidentified live vendor/policy file: ' + str(target))
    if vendor_binary.exists() and digest(vendor_binary) != OLD_BINARY: raise RuntimeError('Live vendor binary changed')
    backup = Path('/opt/hpr/backups') / ('media-broker-security-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    paths = [CONFIG, VIEWER_UNIT, RUNTIME / 'mediamtx', RUNTIME / 'mediamtx.yml'] + [p for _, p in targets]
    manifest = []
    for path in paths:
        manifest.append({'path': str(path), 'existed': path.exists()})
        if path.exists():
            target = backup / str(path).lstrip('/'); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
    for dropin in Path('/etc/systemd/system/hpr-video-compositor.service.d').glob('*.conf'):
        shutil.copy2(dropin, backup / dropin.name)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2)); (backup / 'before.txt').write_text(before)
    print('Protected media rollback backup: ' + str(backup), flush=True)
    old_unit = VIEWER_UNIT.read_bytes(); line = b'Requires=hpr-video-mediamtx.service hpr-video-console.service\n'
    if old_unit.count(line) != 1: raise RuntimeError('Unidentified compositor dependency shape')
    touched = False
    try:
        atomic_write(VIEWER_UNIT, old_unit.replace(line, b'Requires=hpr-video-console.service\nWants=hpr-video-mediamtx.service\n'))
        run('systemd-analyze', 'verify', str(VIEWER_UNIT), '/etc/systemd/system/' + UNIT)
        run('systemctl', 'daemon-reload')
        for relation in ('Requires', 'BindsTo', 'PartOf'):
            if UNIT in run('systemctl', 'show', 'hpr-video-compositor.service', '-p', relation, '--value').split():
                raise RuntimeError('Native viewer still coupled to broker stop')
        with config_lock(CONFIG):
            if CONFIG.read_bytes() != raw or not peers() <= allowed or any(CONFIG.with_name(p).exists() for p in PENDING):
                raise RuntimeError('Configuration or peer changed during preflight')
            touched = True; run('systemctl', 'stop', UNIT)
            for source, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True); pending = target.with_name(target.name + '.security-new')
                if pending.exists(): raise RuntimeError('Unexpected staging file; preserve and inspect')
                shutil.copyfile(source, pending); os.chmod(pending, 0o755 if source == binary else 0o644); pending.replace(target)
            atomic_write(CONFIG, yaml.safe_dump(data, sort_keys=False).encode())
            atomic_write(RUNTIME / 'mediamtx.yml', broker_data)
            pending = RUNTIME / 'mediamtx.security-new'
            if pending.exists(): raise RuntimeError('Unexpected runtime staging file')
            shutil.copyfile(binary, pending); os.chmod(pending, 0o755); pending.replace(RUNTIME / 'mediamtx')
        run('systemctl', 'start', UNIT)
        deadline = time.monotonic() + 25
        while True:
            try:
                after_streams = decodes(updated)
                if after_streams != streams: raise RuntimeError('Codec/resolution/frame rate changed')
                break
            except (subprocess.SubprocessError, ValueError, KeyError):
                if time.monotonic() >= deadline: raise
                time.sleep(1)
        after_captures = capture_ids()
        if fingerprints() != before or any(after_captures.get(k) != v for k,v in captures.items()) or any(digest(Path(p)) != h for p,h in controls.items()):
            raise RuntimeError('Protected process/capture/local tuning changed')
        owner.validate_status(updated, config.get('configuration.trike_hash'))
        if load_config(str(CONFIG)).data != data: raise RuntimeError('Config diverged; preserve central updates')
        (backup / 'validated.json').write_text(json.dumps({'streams': after_streams, 'captures': captures,
            'trusted_readers': sorted(allowed), 'binary_sha256': metadata['binary_sha256']}, indent=2))
        print(json.dumps({'deployed': True, 'version': metadata['version_label'], 'restarted': UNIT,
            'native_capture_hdmi_gpio_pids_unchanged': True, 'tuning_unchanged': True,
            'trusted_readers': sorted(allowed), 'backup': str(backup)}), flush=True)
    except Exception:
        if not touched:
            atomic_write(VIEWER_UNIT, old_unit); run('systemctl', 'daemon-reload'); raise
        run('systemctl', 'stop', UNIT)
        with config_lock(CONFIG):
            if load_config(str(CONFIG)).data not in (data, config.data):
                raise RuntimeError('Central state changed; retain backup for reviewed rollback without overwriting it')
            for item in manifest:
                path = Path(item['path'])
                if path == VIEWER_UNIT: continue
                if item['existed']: shutil.copy2(backup / str(path).lstrip('/'), path)
                elif path.exists(): path.unlink()
        run('systemctl', 'start', UNIT)
        atomic_write(VIEWER_UNIT, old_unit); run('systemctl', 'daemon-reload')
        print('Broker/source/config restored from ' + str(backup), flush=True)
        raise


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

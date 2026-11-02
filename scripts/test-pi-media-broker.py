#!/usr/bin/env python3
"""Compare isolated RTSP brokers and ACLs without changing production services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import yaml


def run(*args, timeout=35):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def fingerprint():
    return run('systemctl', 'show', 'hpr-video-compositor', 'hpr-video-mediamtx', 'hpr-gpio-control',
               '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--probe-sha256', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0: raise RuntimeError('Root required for resource-isolated transient test units')
    stage = args.stage.resolve()
    if stage.parent != Path('/home/admin') or not stage.name.startswith('hpr-media-candidate-'):
        raise RuntimeError('Use a separate private candidate staging directory')
    candidate, probe = stage / 'mediamtx', stage / 'media-broker-probe'
    for path, expected in ((candidate, args.candidate_sha256), (probe, args.probe_sha256)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Candidate/probe hash mismatch')
    baseline = Path('/opt/hpr/gateway/runtime/video/mediamtx')
    if hashlib.sha256(baseline.read_bytes()).hexdigest() != '31f021d518e4da59295be8e7170f09257cc9be04981001da9c2cb9bea30b26e1':
        raise RuntimeError('Production baseline changed; repeat read-only comparison')
    if (stage / 'baseline-mediamtx').exists(): raise RuntimeError('Do not replace existing test evidence')
    shutil.copy2(baseline, stage / 'baseline-mediamtx')
    fixture = stage / 'software-frame.h264'
    run('nice', '-n', '15', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
        '-i', 'color=c=black:s=320x180:r=30', '-frames:v', '1', '-threads', '1',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
        '-x264-params', 'keyint=1', '-f', 'h264', str(fixture))
    before = fingerprint()
    results, units = {}, []
    try:
        for label, port, binary in (('baseline', 18554, stage / 'baseline-mediamtx'), ('candidate', 18555, candidate)):
            with socket.socket() as check:
                if check.connect_ex(('127.0.0.1', port)) == 0: raise RuntimeError('Test port already in use')
            config = {'logLevel': 'warn', 'rtsp': True, 'rtspAddress': '127.0.0.1:' + str(port),
                      'rtspTransports': ['tcp'], 'rtmp': False, 'hls': False, 'webrtc': False,
                      'srt': False, 'moq': False, 'api': False, 'metrics': False, 'pprof': False, 'playback': False,
                      'authMethod': 'internal', 'authInternalUsers': [{'user': 'any', 'pass': '',
                          'ips': ['127.0.0.1/32', '::1/128'], 'permissions': [
                              {'action': action, 'path': path} for action in ('publish','read') for path in ('front','rear')]}],
                      'paths': {path: {'source': 'publisher'} for path in ('front','rear')}}
            config['authInternalUsers'].append({'user':'any','pass':'','ips':['127.0.0.3/32'],
                'permissions':[{'action':'read','path':path} for path in ('front','rear')]})
            file = stage / (label + '.yaml')
            file.write_text(yaml.safe_dump(config, sort_keys=False)); os.chmod(file, 0o644)
            unit = 'hpr-media-isolation-' + label + '-' + str(os.getpid())
            units.append(unit)
            run('systemd-run', '--unit=' + unit, '--property=User=admin', '--property=CPUQuota=15%',
                '--property=MemoryMax=150M', '--property=NoNewPrivileges=yes', '--property=PrivateDevices=yes',
                '--property=ProtectKernelTunables=yes', '--property=ProtectSystem=strict',
                '--property=IPAddressDeny=any', '--property=IPAddressAllow=localhost',
                str(binary), str(file))
            deadline = time.monotonic() + 10
            while True:
                with socket.socket() as check:
                    if check.connect_ex(('127.0.0.1', port)) == 0: break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Isolated broker did not start; inspect transient unit')
                time.sleep(.1)
            time.sleep(1)
            run('systemctl', 'is-active', '--quiet', unit)
            results[label] = {}
            for path in ('front', 'rear'):
                try:
                    output = run(str(probe), '--url', f'rtsp://127.0.0.1:{port}/{path}', '--h264-fixture', str(fixture), '--read-only-source', '127.0.0.3', timeout=30)
                except subprocess.CalledProcessError as exc:
                    (stage / (label + '-' + path + '-failure.txt')).write_text(exc.output)
                    print(exc.output, flush=True)
                    raise
                value = json.loads(output.splitlines()[-1])
                results[label][path] = value
                print(json.dumps({label: {path: value}}), flush=True)
            run('systemctl', 'stop', unit)
        for path in ('front','rear'):
            old, new = results['baseline'][path]['p95_ms'], results['candidate'][path]['p95_ms']
            if new > max(5, old*2+1): raise RuntimeError('Candidate relay delay regressed beyond the conservative comparison bound')
        if fingerprint() != before: raise RuntimeError('Production service fingerprint changed during isolated tests')
        results['production_processes_unchanged'] = True
        (stage / 'probe-results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps({'isolated_tests': 'passed', 'production_processes_unchanged': True}), flush=True)
    finally:
        for unit in units:
            subprocess.run(['systemctl','stop',unit], check=False, capture_output=True, timeout=20)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

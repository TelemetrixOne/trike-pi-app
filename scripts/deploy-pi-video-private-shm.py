#!/usr/bin/env python3
"""Repair native video IPC isolation with one affected-service restart only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SOURCE = Path(__file__).resolve().parents[1]
UNIT = 'hpr-video-compositor.service'
TARGET = Path('/etc/systemd/system/hpr-video-compositor.service.d/90-private-shm.conf')
PROTECTED = ['hpr-gpio-control.service','hpr-gps.service','hpr-heart-rate.service',
             'hpr-tpms.service','hpr-power-cadence.service','hpr-video-mediamtx.service','hpr-trike-config-sync.service']


def run(*args, timeout=25):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def digest(path): return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def fingerprint():
    return run('systemctl','show',*PROTECTED,'-p','Id','-p','MainPID','-p','NRestarts','-p','ActiveState')


def validate():
    pid = run('systemctl','show',UNIT,'-p','MainPID','--value')
    names = run('nsenter','-t',pid,'-m','--','ls','-1','/dev/shm')
    if 'shmpipe.' not in names: raise RuntimeError('No private capture buffers yet')
    result = {}
    for camera in ('front','rear'):
        url='rtsp://127.0.0.1:8554/'+camera
        value=json.loads(run('ffprobe','-v','error','-rtsp_transport','tcp','-show_entries',
                            'stream=codec_name,width,height,avg_frame_rate','-of','json',url,timeout=10))
        run('nice','-n','15','ffmpeg','-nostdin','-v','error','-rtsp_transport','tcp','-i',url,
            '-frames:v','3','-an','-sn','-f','null','-',timeout=10)
        result[camera]=value['streams']
    return {'native_pid':int(pid),'private_buffer_names':names.splitlines(),'streams':result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video-restart-approved',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or not args.video_restart_approved: raise RuntimeError('Root and approved affected-video restart required')
    if TARGET.exists(): raise RuntimeError('Existing IPC drop-in must be preserved and inspected')
    live=Path('/opt/hpr/gateway/runtime/video/hpr-video-native.py')
    if digest(live)!=digest(SOURCE/'bin/hpr-video-native.py'): raise RuntimeError('Unidentified native viewer enhancement')
    files=[Path('/etc/hpr/hpr.yaml'),Path('/etc/hpr/camera-controls.yaml'),Path('/etc/hpr/video-local.yaml')]
    hashes={str(p):digest(p) for p in files};before=fingerprint()
    backup=Path('/opt/hpr/backups')/('video-private-shm-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    (backup/'unit-before.txt').write_text(run('systemctl','cat',UNIT))
    (backup/'before.json').write_text(json.dumps({'protected':before,'configuration_hashes':hashes},indent=2))
    TARGET.parent.mkdir(exist_ok=True)
    shutil.copyfile(SOURCE/'systemd/hpr-video-compositor.service.d/90-private-shm.conf',TARGET);os.chmod(TARGET,0o644)
    try:
        run('systemd-analyze','verify','/etc/systemd/system/'+UNIT)
        run('systemctl','daemon-reload');run('systemctl','restart',UNIT)
        deadline=time.monotonic()+30
        while True:
            try:
                result=validate();break
            except (subprocess.SubprocessError,ValueError,KeyError,RuntimeError):
                if time.monotonic()>=deadline:raise
                time.sleep(1)
        if fingerprint()!=before or any(digest(Path(p))!=v for p,v in hashes.items()):raise RuntimeError('Protected process/configuration changed')
        (backup/'validated.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({'restarted':UNIT,'private_shm':True,'configuration_unchanged':True,
                          'protected_pids_unchanged':True,'validation':result,'backup':str(backup)}),flush=True)
    except Exception:
        print('Video IPC validation incomplete; preserve drop-in/evidence and inspect '+str(backup),flush=True)
        raise


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hpr_gateway.maintenance import guarded_main
    raise SystemExit(guarded_main(main))

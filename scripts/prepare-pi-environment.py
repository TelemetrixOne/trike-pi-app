#!/usr/bin/env python3
"""Build/check a new isolated candidate; never switch services or active venv."""
import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--scope', choices=('all', 'gps'), default='all')
    parser.add_argument('--gps-binding', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or sys.version_info[:2] != (3, 13):
        raise RuntimeError('This lock supports Linux ARM64 with Python 3.13; prepare a separate audited lock for other platforms')
    target = Path(args.destination).resolve()
    if target.exists():
        raise RuntimeError('Candidate directory must not exist; never mutate an active environment')
    if hasattr(os, 'nice'): os.nice(10)
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(target)
    python = target / 'bin/python'
    requirements = Path(__file__).resolve().parents[1] / ('requirements.gps.txt' if args.scope == 'gps' else 'requirements.txt')
    subprocess.run([str(python), '-m', 'pip', 'install', '--prefer-binary', '-r', str(requirements)], check=True)
    if args.gps_binding:
        source = Path(subprocess.check_output([sys.executable, '-c',
            "import gps,pathlib;print(pathlib.Path(gps.__file__).parent)"], text=True).strip()).resolve()
        if source.name != 'gps' or not source.is_relative_to('/usr/lib'):
            raise RuntimeError('GPS binding is not the audited OS package')
        site = Path(subprocess.check_output([str(python), '-c', 'import site;print(site.getsitepackages()[0])'], text=True).strip())
        (site / 'gps').symlink_to(source, target_is_directory=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    modules = 'import paho.mqtt.client,yaml' + (',bleak,gpiozero,adafruit_ads1x15.ads1115' if args.scope == 'all' else '')
    if args.gps_binding: modules += ',gps'
    subprocess.run([str(python), '-I', '-c', modules], check=True)
    print(json.dumps({'candidate': str(target), 'isolated': True, 'activated': False,
                      'validation': 'pip_check_and_import_only_no_gpio_access'}))


if __name__ == '__main__': main()

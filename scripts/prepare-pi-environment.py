#!/usr/bin/env python3
"""Build/check a new isolated candidate; never switch services or active venv."""
import argparse
import hashlib
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
    native = {}
    modules = [('lgpio', 'python3-lgpio'), ('_lgpio', 'python3-lgpio')] if args.scope == 'all' else []
    if args.gps_binding: modules.append(('gps', 'python3-gps'))
    site = Path(subprocess.check_output([str(python), '-c', 'import site;print(site.getsitepackages()[0])'], text=True).strip())
    for module, package in modules:
        source = Path(subprocess.check_output([sys.executable, '-c',
            'import importlib.util;print(importlib.util.find_spec(' + repr(module) + ').origin)'], text=True).strip()).resolve()
        if not source.is_relative_to('/usr/lib'):
            raise RuntimeError('Native binding is not under the audited OS namespace')
        owner = subprocess.check_output(['dpkg-query', '-S', str(source)], text=True).strip().split(': ', 1)[0]
        if owner.split(':')[0] != package:
            raise RuntimeError('Native binding has unexpected package ownership')
        version = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', owner], text=True).strip()
        target_source = source.parent if module == 'gps' else source
        (site / ('gps' if module == 'gps' else source.name)).symlink_to(target_source, target_is_directory=target_source.is_dir())
        native[module] = {'source': str(source), 'package': owner, 'version': version,
                          'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        if module == 'lgpio':
            metadata = json.loads(subprocess.check_output([sys.executable, '-c',
                "import importlib.metadata,json;d=importlib.metadata.distribution('lgpio');print(json.dumps({'path':str(d._path),'version':d.version}))"], text=True))
            info = Path(metadata['path']).resolve()
            if not info.is_relative_to('/usr/lib') or metadata['version'] != '0.2.2.0':
                raise RuntimeError('OS lgpio metadata does not match the audited binding version')
            metadata_file = info / ('PKG-INFO' if info.name.endswith('.egg-info') else 'METADATA')
            metadata_owner = subprocess.check_output(['dpkg-query', '-S', str(metadata_file)], text=True).strip().split(': ', 1)[0]
            if metadata_owner.split(':')[0] != package:
                raise RuntimeError('Native binding metadata has unexpected ownership')
            (site / info.name).symlink_to(info, target_is_directory=True)
            native[module]['metadata_sha256'] = hashlib.sha256(metadata_file.read_bytes()).hexdigest()
    # Register the audited OS binding first so Blinka's dependency resolution
    # recognises it and never tries to compile a second lgpio implementation.
    subprocess.run([str(python), '-m', 'pip', 'install', '--prefer-binary', '-r', str(requirements)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    imports = 'import paho.mqtt.client,yaml' + (',bleak,gpiozero,lgpio,adafruit_ads1x15.ads1115' if args.scope == 'all' else '')
    if args.gps_binding: imports += ',gps'
    subprocess.run([str(python), '-I', '-c', imports], check=True)
    (target / 'native-bindings.json').write_text(json.dumps(native, indent=2))
    print(json.dumps({'candidate': str(target), 'isolated': True, 'activated': False,
                      'native_bindings': native, 'validation': 'pip_check_and_import_only_no_gpio_access'}))


if __name__ == '__main__': main()

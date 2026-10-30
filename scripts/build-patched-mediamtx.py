#!/usr/bin/env python3
"""Build a separate pinned MediaMTX ARM64 candidate; never replace live/vendor files."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

COMMIT = '9540cb6c1d9f1e9351fcc4ded3afc249ffff22bc'
ARCHIVE_SHA256 = '48e9cef4b82b532b70ae1965549c22b4c3692501d841c7e88e17113205f0376c'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', required=True, type=Path)
    parser.add_argument('--source-archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists(): raise RuntimeError('Preserve existing build; output must not exist')
    if subprocess.check_output([str(args.go), 'env', 'GOVERSION'], text=True).strip() != 'go1.27.3':
        raise RuntimeError('Build requires the audited Go1.27.3 toolchain')
    data = args.source_archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError('Upstream source archive hash mismatch')
    args.output.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        archive.extractall(args.output / 'source', filter='data')
    source = args.output / 'source' / ('mediamtx-' + COMMIT)
    environment = dict(os.environ, GOTOOLCHAIN='local', GOMAXPROCS='2', CGO_ENABLED='0',
                       GOPATH=str((args.output / 'go-tools').resolve()),
                       PATH=str(args.go.parent.resolve()) + os.pathsep + os.environ.get('PATH', ''))
    def go(*values, target=False):
        env = dict(environment)
        if target: env.update(GOOS='linux', GOARCH='arm64')
        subprocess.run([str(args.go.resolve()), *values], cwd=source, env=env, check=True)
    go('get', 'golang.org/x/net@v0.62.0')
    go('mod', 'verify')
    patch = Path(__file__).resolve().parents[1] / 'vendor/mediamtx/patches/git-managed-upgrade.go'
    # Remove only the four identified files in this fresh, hash-verified source.
    # The unused updater pulled legacy OpenPGP/Git dependencies into the broker.
    for name in ('upgrade.go', 'check_version.go', 'extract_executable_other.go', 'extract_executable_windows.go'):
        (source / 'internal/upgrade' / name).unlink()
    (source / 'internal/upgrade/upgrade.go').write_bytes(patch.read_bytes())
    # TM1 cameras are independent USB capture publishers. Use the upstream
    # unsupported-CSI source implementation on both architectures; do not bundle
    # unrelated libcamera/IPA binaries inside this RTSP broker.
    camera = source / 'internal/staticsources/rpicamera'
    for name in ('camera_arm_.go', 'camera_linux_arm.go', 'camera_linux_arm64.go',
                 'camera_params_arm_.go', 'pipe_arm_.go', 'source_arm_.go', 'supports_hardware_h264_arm_.go'):
        (camera / name).unlink()
    other = (camera / 'source_other.go').read_text()
    prefix = '//go:build !linux || (!arm && !arm64)\n\n'
    if not other.startswith(prefix): raise RuntimeError('Upstream CSI stub changed; review required')
    (camera / 'source_other.go').write_text(other[len(prefix):])
    # The HLS generator verifies its asset against a source-pinned SHA256.
    # Do not run the Git version getter or CSI binary downloader.
    go('generate', './internal/servers/hls')
    # Upstream embeds this generated version file. No race/vehicle data is used.
    (source / 'internal/core/VERSION').write_text('v1.21.3-tm1.1\n')
    binary = (args.output / 'mediamtx').resolve()
    go('build', '-trimpath', '-buildvcs=false', '-o', str(binary), '.', target=True)
    environment_amd64 = dict(environment, GOOS='linux', GOARCH='amd64')
    subprocess.run([str(args.go.resolve()), 'build', '-trimpath', '-buildvcs=false',
                    '-o', str((args.output / 'mediamtx-linux-amd64').resolve()), '.'],
                   cwd=source, env=environment_amd64, check=True)
    modules = subprocess.check_output([str(args.go.resolve()), 'version', '-m', str(binary)], text=True)
    (args.output / 'build-modules.txt').write_text(modules)
    (args.output / 'go.mod').write_bytes((source / 'go.mod').read_bytes())
    (args.output / 'go.sum').write_bytes((source / 'go.sum').read_bytes())
    go('install', 'golang.org/x/vuln/cmd/govulncheck@v1.9.0')
    tool = args.output / 'go-tools/bin' / ('govulncheck.exe' if os.name == 'nt' else 'govulncheck')
    with (args.output / 'govulncheck-binary.jsonl').open('w') as report:
        result = subprocess.run([str(tool.resolve()), '-mode=binary', '-json', str(binary)],
                                env=environment, stdout=report, check=False)
    with (args.output / 'govulncheck-amd64.jsonl').open('w') as report:
        amd64_result = subprocess.run([str(tool.resolve()), '-mode=binary', '-json', str((args.output / 'mediamtx-linux-amd64').resolve())],
                                     env=environment, stdout=report, check=False)
    metadata = {'source_commit': COMMIT, 'source_archive_sha256': ARCHIVE_SHA256,
                'upstream_tag': 'v1.21.3', 'version_label': 'v1.21.3-tm1.1',
                'toolchain': 'go1.27.3', 'patch': 'golang.org/x/net v0.62.0',
                'updater_patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
                'feature_profile': 'Git-managed updater; upstream CSI stub; USB capture remains external',
                'go_mod_sha256': hashlib.sha256((source / 'go.mod').read_bytes()).hexdigest(),
                'go_sum_sha256': hashlib.sha256((source / 'go.sum').read_bytes()).hexdigest(),
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'amd64_sha256': hashlib.sha256((args.output / 'mediamtx-linux-amd64').read_bytes()).hexdigest(),
                'govulncheck_version': 'v1.9.0', 'govulncheck_exit_code': result.returncode,
                'govulncheck_amd64_exit_code': amd64_result.returncode,
                'deployed': False}
    (args.output / 'provenance.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata), flush=True)


if __name__ == '__main__': main()

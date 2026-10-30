#!/usr/bin/env python3
"""Collect exact module notice files, without executing or extracting source code.

The result is evidence for review, not automatic licence or advisory clearance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile


def escape(value):
    return ''.join('!' + c.lower() if c.isupper() else c for c in value)


def collect(component, output, rules):
    identity = component['name'] + '@' + component['version']
    folder = hashlib.sha256(identity.encode()).hexdigest()[:16]
    result = {'module': component['name'], 'version': component['version'], 'files': []}
    url = 'https://proxy.golang.org/' + escape(component['name']) + '/@v/' + escape(component['version']) + '.zip'
    result['source'] = url
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            data = response.read(25 * 1024 * 1024 + 1)
        if len(data) > 25 * 1024 * 1024:
            raise ValueError('module archive over limit')
        result['archive_sha256'] = hashlib.sha256(data).hexdigest()
        prefix = component['name'] + '@' + component['version'] + '/'
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                if not item.filename.startswith(prefix) or item.is_dir():
                    continue
                relative = item.filename[len(prefix):]
                basename = relative.rsplit('/', 1)[-1]
                if not re.match(r'^(LICENSE|LICENCE|COPYING|NOTICE|PATENTS|AUTHORS)(?:[._-].*)?$', basename, re.I):
                    continue
                if item.file_size > 1024 * 1024 or len(result['files']) >= 256:
                    raise ValueError('notice collection over limit')
                notice = archive.read(item)
                notice.decode('utf-8')
                name = hashlib.sha256(relative.encode()).hexdigest()[:12] + '.txt'
                target = output / folder / name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(notice)
                result['files'].append({'upstream_path': relative, 'saved_path': folder + '/' + name,
                                        'sha256': hashlib.sha256(notice).hexdigest()})
            rule = rules.get(component['name'])
            if not result['files'] and rule and rule['version'] == component['version']:
                code = archive.read(prefix + rule['source_file'])
                header = []
                for line in code.decode('utf-8').splitlines():
                    if line.startswith('//') or not line.strip(): header.append(line)
                    else: break
                notice = ('\n'.join(header).rstrip()+'\n').encode('utf-8')
                if hashlib.sha256(notice).hexdigest() != rule['header_sha256']:
                    raise ValueError('Compiled-package licence header changed; review required')
                name = 'COMPILED-PACKAGE-LICENSE.txt'
                target = output / folder / name; target.parent.mkdir(exist_ok=True); target.write_bytes(notice)
                result['files'].append({'upstream_path': rule['source_file'] + ' (complete licence header)',
                    'saved_path': folder+'/'+name, 'sha256': hashlib.sha256(notice).hexdigest(),
                    'source_file_sha256': hashlib.sha256(code).hexdigest(),
                    'scope': 'Only compiled packages: ' + ', '.join(rule['compiled_packages'])})
                result['compiled_package_scope'] = rule['compiled_packages']
        result['status'] = 'collected_requires_review' if result['files'] else 'missing_notice_requires_review'
    except Exception as exc:
        result['status'] = 'failed_requires_review'
        result['error'] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((args.inventory or root / 'vendor/mediamtx/build-components.json').read_text())
    rules_path = root / 'vendor/mediamtx/package-notice-rules.json'
    rules = json.loads(rules_path.read_text()) if rules_path.exists() else {}
    if hashlib.sha256((args.binary or root / inventory['artifact']).read_bytes()).hexdigest() != inventory['sha256']:
        raise RuntimeError('Binary changed; regenerate and review build inventory first')
    if args.output.exists():
        raise RuntimeError('Output must not exist; never replace unidentified notice files')
    args.output.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda component: collect(component, args.output, rules), inventory['dependencies']))
    report = {'scope': 'Module source notices; no executable source extracted. Review required for binary redistribution.',
              'binary_sha256': inventory['sha256'], 'modules': records}
    (args.output / 'index.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'modules': len(records), 'collected': sum(bool(x['files']) for x in records),
                      'requires_review': [x['module'] for x in records if not x['files']]}))


if __name__ == '__main__': main()

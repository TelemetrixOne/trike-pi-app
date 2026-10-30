#!/usr/bin/env python3
"""Query OSV for declared versions; this is not a reachability/security clearance."""
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    components = []
    for line in (root / 'requirements.lock').read_text().splitlines():
        if '==' in line and not line.startswith('#'):
            name, version = line.split('==')
            components.append({'package': {'name': name, 'ecosystem': 'PyPI'}, 'version': version})
    inventory = json.loads((root / 'vendor/mediamtx/build-components.json').read_text())
    for component in [inventory['main'], *inventory['dependencies']]:
        components.append({'package': {'name': component['name'], 'ecosystem': 'Go'}, 'version': component['version']})
    components.append({'package': {'name': 'stdlib', 'ecosystem': 'Go'}, 'version': inventory['go_version'].removeprefix('go')})
    request = urllib.request.Request('https://api.osv.dev/v1/querybatch',
        data=json.dumps({'queries': components}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        values = json.load(response)['results']
    if len(values) != len(components):
        raise RuntimeError('Incomplete advisory response')
    print(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'https://api.osv.dev/v1/querybatch',
        'scope': 'Declared versions only; OS native patches, runtime reachability, unlisted dependencies and non-Go media packages require separate review',
        'components': [{**component, 'advisories': value.get('vulns', [])} for component, value in zip(components, values)]}, indent=2))


if __name__ == '__main__': main()

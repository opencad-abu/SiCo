"""Verify byte dependencies through the effective package's bound resource roots."""

import hashlib
from pathlib import Path

from .jsonio import fail


def check(package, ref):
    dep = package.manifest['dependencies'][ref]
    if dep['kind'] not in {'file', 'model'} or dep['method'] != 'sha256-bytes-v1':
        fail('Unsupported source dependency: ' + ref, 'pdk_source_changed')
    resource = package.document('file.json')['items'].get(dep['target'], {})
    root = package.roots.get(resource.get('root'))
    if not root or resource.get('dependency') != ref:
        fail('Unbound source resource: ' + ref, 'pdk_source_changed')
    path = Path(root) / resource['path']
    digest = hashlib.sha256()
    try:
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
    except OSError as exc:
        fail('Cannot verify source resource: ' + ref + ': ' + str(exc), 'pdk_source_changed')
    if dep['fingerprint'] != 'sha256:' + digest.hexdigest():
        fail('Source resource changed: ' + ref, 'pdk_source_changed')

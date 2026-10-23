"""Verify byte dependencies through the effective package's bound resource roots.

``status`` reports one dependency without raising; ``check`` is the fail-closed
gate every consumer shares, so the impact review never grows a second opinion
about what a valid byte resource is.
"""

import hashlib
from pathlib import Path

from .jsonio import fail


def status(package, ref):
    """Non-raising outcome for one byte dependency: {status, detail, resource, actual}."""
    dep = package.manifest['dependencies'].get(ref)
    if not dep or dep['kind'] not in {'file', 'model'} or dep['method'] != 'sha256-bytes-v1':
        return {'status': 'unsupported', 'detail': 'Unsupported source dependency: ' + ref,
                'resource': None, 'actual': None}
    resource = package.document('file.json')['items'].get(dep['target'], {})
    root = package.roots.get(resource.get('root'))
    if not root or resource.get('dependency') != ref:
        return {'status': 'unbound', 'detail': 'Unbound source resource: ' + ref, 'resource': None,
                'actual': None}
    path = Path(root) / resource['path']
    digest = hashlib.sha256()
    try:
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
    except OSError as exc:
        return {'status': 'missing',
                'detail': 'Cannot verify source resource: ' + ref + ': ' + str(exc),
                'resource': resource['path'], 'actual': None}
    actual = 'sha256:' + digest.hexdigest()
    if dep['fingerprint'] != actual:
        return {'status': 'changed', 'detail': 'Source resource changed: ' + ref,
                'resource': resource['path'], 'actual': actual}
    return {'status': 'ok', 'detail': None, 'resource': resource['path'], 'actual': actual}


def check(package, ref):
    outcome = status(package, ref)
    if outcome['status'] != 'ok':
        fail(outcome['detail'], 'pdk_source_changed')

"""Locate the blast radius of source changes over the declared dependency graph.

Read-only. Every file/model byte dependency is re-hashed through its bound
resource root; OA-backed dependencies (CDF, symbol geometry, interfaces) are
mapped from the documents that declare them - and, for one device at a time,
re-checked live against the captured fingerprints. Documents, devices and user
confirmations outside the listed scope keep their current validity and are
reported as unaffected; nothing is collected and no index entry changes.
"""

from collections import defaultdict

from ..pdk_normalize import context, detail
from . import VERSION
from .ingest import documents
from .jsonio import fail
from .provenance import verify
from .resource_check import status as resource_status
from .source_check import compare
from .workspace import select

LIVE_KINDS = ('cdf', 'interface', 'symbol', 'callback', 'library', 'technology')
MAX_CHANGED = 20
MAX_DEVICES_PER_DEP = 10
MAX_CONFIRMATIONS_PER_DEP = 8


def graph(package):
    """Dependency usage over every logical document: ref -> documents/devices."""
    usage = defaultdict(lambda: {'documents': [], 'devices': set()})
    devices = package.document('device.json')['items']
    by_dir = {row['dir']: key for key, row in devices.items() if 'dir' in row}
    docs = documents(package)
    device_of = {}
    for name, value in sorted(docs.items()):
        device = by_dir.get(name.rsplit('/', 1)[0]) if '/' in name else None
        device_of[name] = device
        for ref in value.get('depends_on', []):
            usage[ref]['documents'].append(name)
            if device:
                usage[ref]['devices'].add(device)
    return docs, device_of, usage


def confirmations(package, names, docs):
    """User confirmation source ids referenced inside the given documents."""
    sources = package.document('sources.json')['items']
    found = set()
    for name in names:
        value = docs[name]
        refs = {value.get('source')} | set(value.get('evidence', {}).values())
        found.update(ref for ref in refs if sources.get(ref, {}).get('kind') == 'user')
    return found


def _entry(package, docs, usage, ref, declared, status, detail, resource=None):
    documents_here = usage.get(ref, {'documents': [], 'devices': set()})
    devices = sorted(documents_here['devices'])
    found = confirmations(package, documents_here['documents'], docs)
    return {
        'ref': ref, 'kind': declared['kind'], 'status': status, 'detail': detail,
        'resource': resource,
        'affected_documents': len(documents_here['documents']),
        'affected_devices': devices[:MAX_DEVICES_PER_DEP],
        'devices_truncated': len(devices) > MAX_DEVICES_PER_DEP,
        'affected_confirmations': sorted(found)[:MAX_CONFIRMATIONS_PER_DEP],
        'confirmations_truncated': len(found) > MAX_CONFIRMATIONS_PER_DEP,
    }


def review(workspace, environment, bridge, args):
    package = verify(select(workspace, args['library'], environment))
    docs, device_of, usage = graph(package)
    manifest = package.manifest
    byte_rows = {}
    for ref, declared in sorted(manifest['dependencies'].items()):
        if declared['kind'] in {'file', 'model'}:
            byte_rows[ref] = resource_status(package, ref)
    changed = []
    for ref, outcome in sorted(byte_rows.items()):
        if outcome['status'] != 'ok':
            changed.append(_entry(package, docs, usage, ref, manifest['dependencies'][ref],
                                  outcome['status'], outcome['detail'], outcome['resource']))
    live = {'dependencies': 0, 'kinds': {}, 'devices': set()}
    for ref, declared in manifest['dependencies'].items():
        if declared['kind'] in LIVE_KINDS:
            live['dependencies'] += 1
            live['kinds'][declared['kind']] = live['kinds'].get(declared['kind'], 0) + 1
            live['devices'].update(usage.get(ref, {}).get('devices', ()))

    device_check = None
    if args.get('device'):
        if bridge is None:
            fail('Live device checks require the session bridge', 'bridge_unavailable')
        key = args['device']
        row = package.document('device.json')['items'].get(key)
        if not row or not isinstance(row['view'], str):
            fail('Select an observed device with a symbol view', 'device_unavailable')
        target = {'library': manifest['libraries'][row['library']]['name'],
                  'cell': row['cell'], 'view': row['view']}
        capture = bridge.capture('device', target)
        value = detail(capture['data'], context(capture['context']))
        refs = set()
        for name, device in device_of.items():
            if device == key:
                refs.update(docs[name].get('depends_on', []))
        outcome = compare(package, key, value, sorted(refs))
        device_check = {'device': key, 'binding': outcome['binding'], 'refs': outcome['refs']}
        for r in outcome['refs']:
            if r['status'] != 'ok':
                changed.append(_entry(package, docs, usage, r['ref'],
                                      manifest['dependencies'][r['ref']], r['status'],
                                      r['detail']))

    all_devices = {key for key, row in package.document('device.json')['items'].items()
                   if 'dir' in row}
    affected_docs = {name for ref in {c['ref'] for c in changed}
                     for name in usage.get(ref, {}).get('documents', ())}
    affected_devices = {device for ref in {c['ref'] for c in changed}
                        for device in usage.get(ref, {}).get('devices', ())}
    user_sources = {ref for ref, row in package.document('sources.json')['items'].items()
                    if row.get('kind') == 'user'}
    affected_confirmations = confirmations(package, affected_docs, docs)
    status = 'incomplete' if changed else 'ok'
    return {
        'schema_version': VERSION,
        'revision': package.revision,
        'status': status,
        'byte_dependencies': {
            'checked': len(byte_rows),
            'ok': sum(1 for outcome in byte_rows.values() if outcome['status'] == 'ok'),
            'changed': [ref for ref, outcome in sorted(byte_rows.items())
                        if outcome['status'] == 'changed'],
            'missing': [ref for ref, outcome in sorted(byte_rows.items())
                        if outcome['status'] == 'missing'],
            'unbound': [ref for ref, outcome in sorted(byte_rows.items())
                        if outcome['status'] == 'unbound']},
        'live_required': {'dependencies': live['dependencies'], 'kinds': live['kinds'],
                          'devices': len(live['devices']),
                          'note': 'CDF/symbol/interface dependencies cannot be re-hashed; supply '
                                  'device to re-check that device live against its captured fingerprints'},
        'impact': {'changed': changed[:MAX_CHANGED],
                   'truncated': len(changed) > MAX_CHANGED,
                   'unaffected': {
                       'documents': len(docs) - len(affected_docs),
                       'devices': len(all_devices) - len(affected_devices),
                       'confirmations': len(user_sources) - len(affected_confirmations),
                       'note': 'unaffected documents, devices and confirmations are outside the '
                               'listed scope and keep their current sources and validity'}},
        'device_check': device_check,
        'next_action': 'get_pdk_data',
    }

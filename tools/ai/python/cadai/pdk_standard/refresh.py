"""Live re-verification of requested dependency refreshes for the baseline rebase.

The operator names affected objects (observed devices and/or byte resources);
this module reads the live sources, compares them against the declared
fingerprints and returns exactly what actually changed together with the live
fingerprints that now re-verify it. Declared records that cannot be held
against the live capture (identity drift, legacy captures, unsupported kinds)
are refused instead of being refreshed blindly.
"""

from ..pdk_normalize import context, detail
from .jsonio import fail
from .resource_check import status as resource_status
from .source_check import outcome

MAX_DEVICES = 8
MAX_RESOURCES = 32
LOGICAL = ('cdf.json', 'symbol.json', 'simulation.json')


def targets_from(args):
    """The validated (devices, resources) refresh targets of a request."""
    devices = args.get('refresh_devices') or []
    resources = args.get('refresh_resources') or []
    if not isinstance(devices, list) or not isinstance(resources, list):
        fail('refresh_devices and refresh_resources must be arrays')
    if len(devices) > MAX_DEVICES or len(resources) > MAX_RESOURCES:
        fail('Too many refresh targets for one review')
    if any(not isinstance(item, str) or not item for item in devices + resources):
        fail('Invalid refresh target')
    return devices, resources


def current_record(package, ref):
    """The effective declared record: baseline dependency or overlay addition."""
    if ref in package.dependency_updates:
        return package.dependency_updates[ref]
    return package.manifest['dependencies'].get(ref)


def plan(package, bridge, devices, resources):
    """{changed: {ref: {kind, device, from, to, resource}}, unchanged: [...], devices: [...]}."""
    deps = package.manifest['dependencies']
    changed, unchanged = {}, []
    for ref in resources:
        declared = deps.get(ref)
        if not declared or declared['kind'] not in {'file', 'model'}:
            fail('refresh_resources accepts file/model byte dependencies: ' + ref)
        state = resource_status(package, ref)
        if state['status'] == 'ok':
            unchanged.append(ref)
        elif state['status'] == 'changed':
            changed[ref] = {'kind': declared['kind'], 'device': None,
                            'from': declared['fingerprint'], 'to': state['actual'],
                            'resource': state['resource']}
        else:
            fail('Cannot refresh ' + ref + ': ' + state['detail'], 'pdk_source_changed')
    for key in devices:
        if bridge is None:
            fail('Device re-verification requires the session bridge', 'bridge_unavailable')
        row = package.document('device.json')['items'].get(key)
        if not row or not isinstance(row['view'], str) or 'dir' not in row:
            fail('Select an observed device with a symbol view', 'device_unavailable')
        target = {'library': package.manifest['libraries'][row['library']]['name'],
                  'cell': row['cell'], 'view': row['view']}
        capture = bridge.capture('device', target)
        value = detail(capture['data'], context(capture['context']))
        refs = set()
        for suffix in LOGICAL:
            name = row['dir'] + '/' + suffix
            if package.available(name):
                refs.update(package.document(name)['depends_on'])
        for ref in sorted(refs):
            state = outcome(package, key, value, ref)
            if state['status'] == 'ok':
                unchanged.append(ref)
            elif state['status'] == 'changed':
                changed[ref] = {'kind': state['kind'], 'device': key,
                                'from': deps[ref]['fingerprint'], 'to': state['fingerprint'],
                                'resource': None}
            else:
                fail('Cannot refresh ' + ref + ': ' + state['detail'], 'pdk_source_changed')
    return {'changed': changed, 'unchanged': sorted(set(unchanged)), 'devices': list(devices)}

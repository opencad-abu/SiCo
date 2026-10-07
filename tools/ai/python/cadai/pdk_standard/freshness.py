"""Read-only source checks for the exact cells affected by an interactive update."""

from ..pdk_normalize import context, detail
from .jsonio import fail
from .patches import tokens, pointer


def check(bridge, package, diffs):
    devices = package.document('device.json')['items']
    selected = set()
    for diff in diffs:
        if diff['file'] == 'device.json':
            for row in diff['changes']:
                parts = tokens(row['path'])
                if len(parts) >= 2 and parts[0] == 'items':
                    selected.add(parts[1])
                else:
                    selected.update(devices)
        else:
            selected.update(key for key, row in devices.items() if diff['file'].startswith(row.get('dir', '\0') + '/'))
    for key in sorted(selected):
        row = devices[key]
        if not isinstance(row['view'], str):
            prefix = pointer('items', key, 'use') + '/'
            edits = [edit for diff in diffs if diff['file'] == 'device.json' for edit in diff['changes']
                     if edit['path'].startswith(pointer('items', key) + '/')]
            reason = pointer('items', key, 'reason')
            prohibitions = [edit for edit in edits if edit['path'].startswith(prefix)]
            if prohibitions and all(edit.get('after') == 'deny' for edit in prohibitions) and all(
                    edit['path'].startswith(prefix) or edit['path'] == reason or edit['path'].startswith(reason + '/')
                    for edit in edits):
                continue  # A conservative prohibition does not assert source availability.
            fail('Cannot authorize a cell without an observed symbol view', 'pdk_source_changed')
        target = {'library': package.manifest['libraries'][row['library']]['name'],
                  'cell': row['cell'], 'view': row['view']}
        capture = bridge.capture('device', target)
        current = detail(capture['data'], context(capture['context']))
        required = set()
        for suffix in ('cdf.json', 'symbol.json', 'simulation.json'):
            path = row.get('dir', '') + '/' + suffix
            if package.available(path):
                required.update(package.document(path)['depends_on'])
        if not required:
            fail('Cell source has not been captured', 'pdk_source_changed')
        from .source_check import observed
        observed(package, key, current, required)


def check_publication(bridge, package):
    from .readiness import qualified
    check(bridge, package, [{'file': 'device.json', 'changes': [
        {'path': pointer('items', key, 'use', purpose), 'after': 'allow'}
        for key, _, purpose in qualified(package)]}])

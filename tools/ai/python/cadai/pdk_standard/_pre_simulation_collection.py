"""Assemble the simulation document from a captured device and the model decks.

The builder binds the captured CDF simInfo to the decks: the CDF model field
names the netlist identity, each simulator's default-corner entries resolve
that identity to its ports and configuration ids, and the projection module
turns the two simInfo entries into standard interfaces. Every missing piece
is reported instead of guessed.
"""

from copy import deepcopy
from pathlib import Path

from .jsonio import fail, fingerprint
from .model_decks import resolve
from .simulation_interfaces import TARGETS, project

METHOD = 'sico-siminfo-deck-projection-v1'
INTERFACE_METHOD = 'sico-siminfo-json-v1'


def entries(package, simulator):
    """Default-corner deck entries: (config id, section, path, loader)."""
    if not package.available('model.json'):
        return []
    model = package.document('model.json')
    corner = model['corners'].get(simulator + '.tt')
    if not corner or not package.available('file.json'):
        return []
    files = package.document('file.json')['items']
    rows = []
    for config_id in corner['configs']:
        config = model['configs'].get(config_id) or {}
        if config.get('simulator') != simulator or not config.get('includes'):
            continue
        include = config['includes'][0]
        item = files.get(include['file_ref']) or {}
        root = package.roots.get(item.get('root'))
        if not root or not item.get('path'):
            fail('Model configuration file is unavailable: ' + config_id)
        path = Path(root) / item['path']

        def load(target, base=path.parent):
            return (base / target).read_text(errors='ignore')

        rows.append((config_id, include['section'], str(path), load))
    return rows


def confirmed_setup(package):
    """The PDK-level setup already confirmed by any collected simulation file."""
    for row in package.document('device.json')['items'].values():
        if 'dir' not in row:
            continue
        name = row['dir'] + '/simulation.json'
        if not package.available(name):
            continue
        setup = package.document(name).get('setup')
        if setup:
            return deepcopy(setup)
    return None


def build(package, row, value, source, derived, net_name=None):
    """(document, dependency, note) for the simulation file of one device.

    net_name overrides the CDF model default only for a user-ruled vendor
    anomaly (the ruling is recorded as a user source by the caller); the CDF
    field itself is never rewritten.
    """
    if not package.available(row['dir'] + '/cdf.json'):
        return None, None, 'cdf missing'
    model = ((package.document(row['dir'] + '/cdf.json')['parameters']).get('model') or {})
    if net_name is None:
        net_name = model.get('default')
    if not isinstance(net_name, str) or not net_name:
        return None, None, 'netlist name unknown'
    refs, decks = {}, {}
    for simulator in TARGETS:
        rows = entries(package, simulator)
        if not rows:
            return None, None, 'model corner missing: ' + simulator
        configs, ports, kind = resolve(net_name, simulator, rows)
        if not configs or kind is None:
            return None, None, 'deck declaration missing: ' + simulator
        refs[simulator] = configs
        decks[simulator] = {'ports': ports, 'kind': kind}
    defaults = {p['name']: (p.get('default') or {}).get('value')
                for p in (value.get('parameters') or {}).get('items') or [] if p.get('name')}
    interfaces, missing = project((value.get('simulators') or {}).get('items'),
                                  net_name, refs, decks,
                                  terminals=set(package.document(row['dir'] + '/symbol.json')['terminals']),
                                  defaults=defaults)
    if missing:
        return None, None, 'simInfo missing: ' + ', '.join(missing)
    interface_ref = row['dir'] + '_interface'
    dependency = {
        'kind': 'interface',
        'target': {**value['target'], 'library': row['library']},
        'fingerprint': fingerprint(value['simulators']['items']),
        'method': INTERFACE_METHOD,
    }
    document = {
        'source': source,
        'depends_on': sorted({row['dir'] + '_cdf', interface_ref}),
        'interfaces': interfaces,
        'evidence': {'/interfaces': derived},
    }
    setup = confirmed_setup(package)
    if setup:
        document['setup'] = setup
        document['evidence']['/setup'] = derived
    return document, {interface_ref: dependency}, None

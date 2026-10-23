"""Assemble the simulation document from a captured device and the model decks.

The builder binds the captured CDF simInfo to the decks: the CDF model field
names the netlist identity, each simulator's default-corner entries resolve
that identity to its ports and configuration ids, and the projection module
turns the two simInfo entries into standard interfaces. Every missing piece
is reported instead of guessed.

Without decks the same evidence chain continues through the simulator's own
builtin primitives: the recorded builtin probe supplies the netlist line the
projection must reproduce, and a cell the probe recorded as contributing no
element gets an interface-free document instead of an invented interface.
"""

from copy import deepcopy
from pathlib import Path

from ..pdk_errors import PdkUnavailable
from .jsonio import fail, fingerprint
from .model_decks import resolve, resolve_detail
from .patches import pointer
from .simulation_interfaces import TARGETS, project
from .simulation_builtin import METHOD as BUILTIN_METHOD
from .simulation_builtin import project as builtin_project

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
        for include in config['includes']:
            item = files.get(include['file_ref']) or {}
            root = package.roots.get(item.get('root'))
            if not root or not item.get('path'):
                fail('Model configuration file is unavailable: ' + config_id)
            path = Path(root) / item['path']

            def load(target, base=path.parent):
                return (base / target).read_text(errors='ignore')

            rows.append((config_id, include.get('section'), str(path), load))
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


def build(package, row, value, source, derived, net_name=None, setup=None):
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
        return None, None, 'netlist name unknown: CDF model default is absent'
    refs, decks = {}, {}
    for simulator in TARGETS:
        rows = entries(package, simulator)
        if not rows:
            if not package.available('model.json'):
                return None, None, 'model configuration absent (model.json)'
            return None, None, 'model configuration has no default corner for ' + simulator
        configs, ports, kind = resolve(net_name, simulator, rows)
        if not configs or kind is None:
            return None, None, f'deck declaration missing for {simulator}: {net_name}'
        model_type = None
        if kind == 'model':
            _, _, _, model_type = resolve_detail(net_name, simulator, rows)
        refs[simulator] = configs
        decks[simulator] = {'ports': ports, 'kind': kind, 'model_type': model_type}
    defaults = {p['name']: (p.get('default') or {}).get('value')
                for p in (value.get('parameters') or {}).get('items') or [] if p.get('name')}
    try:
        interfaces, missing = project(
            (value.get('simulators') or {}).get('items'), net_name, refs, decks,
            terminals=set(package.document(row['dir'] + '/symbol.json')['terminals']),
            defaults=defaults)
    except PdkUnavailable as exc:
        return None, None, 'interface projection failed: ' + str(exc)
    if missing:
        return None, None, 'CDF simInfo missing for: ' + ', '.join(missing)
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
    setup = confirmed_setup(package) or setup
    if setup:
        document['setup'] = deepcopy(setup)
        document['evidence']['/setup'] = derived
    return document, {interface_ref: dependency}, None


def build_builtin(package, row, value, source, derived, probe):
    """(document, dependency, note) for a simulator-builtin simulation file.

    The probe carries the recorded netlist lines that the projection must
    agree with; a device the probe does not cover stays a reported gap. A
    cell the probe recorded as emitting no element at all (a global marker
    such as gnd) becomes an interface-free document whose only basis is that
    recorded run.
    """
    observed = probe['devices'].get(row['cell'])
    if not observed:
        return None, None, 'builtin probe evidence missing: ' + str(row['cell'])
    items = (value.get('simulators') or {}).get('items') or []
    declared = {item['name'] for item in items if item.get('name') in TARGETS}
    interface_ref = row['dir'] + '_interface'
    if not declared:
        invisible = {simulator for simulator in TARGETS
                     if (observed.get(simulator) or {}).get('element') is False}
        if invisible != set(TARGETS):
            return None, None, 'netlist-invisible device lacks a full probe basis: ' + str(row['cell'])
        document = {
            'source': source,
            'depends_on': sorted({row['dir'] + '_cdf', row['dir'] + '_symbol'}),
            'interfaces': {},
            'evidence': {'/interfaces': probe['sources']['spectre']},
        }
        return document, {}, None
    dependency = {
        'kind': 'interface',
        'target': {**value['target'], 'library': row['library']},
        'fingerprint': fingerprint(items),
        'method': BUILTIN_METHOD,
    }
    cdf = package.document(row['dir'] + '/cdf.json')
    symbol = package.document(row['dir'] + '/symbol.json')
    try:
        interfaces, missing = builtin_project(items, cdf, observed, set(symbol['terminals']))
    except PdkUnavailable as exc:
        return None, None, 'builtin interface projection failed: ' + str(exc)
    if missing:
        return None, None, 'builtin simInfo missing: ' + ', '.join(missing)
    evidence = {'/interfaces': derived, '/setup': derived}
    for simulator in interfaces:
        evidence[pointer('interfaces', simulator, 'name')] = probe['sources'][simulator]
        evidence[pointer('interfaces', simulator, 'model_source')] = probe['sources'][simulator]
    document = {
        'source': source,
        'depends_on': sorted({row['dir'] + '_cdf', interface_ref}),
        'interfaces': interfaces,
        'setup': deepcopy(probe['setup']),
        'evidence': evidence,
    }
    return document, {interface_ref: dependency}, None

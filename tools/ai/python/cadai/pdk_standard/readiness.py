"""Generation acceptance for the declared circuit and testbench device set."""

from decimal import Decimal

from ..simulation_schema import option_problems
from .constraints import unknown
from .interface import parameters
from .jsonio import fail
from .patches import pointer
from .policy_modes import operands
from .query import missing
from .publication import FEATURE, PROFILE
from .simulation_interfaces import IPAR_CAPABILITY

PURPOSES = ('circuit', 'testbench')
# 'deg' is consumer-supported for AC phase inputs (spectre documents phase in Deg).
UNITS = {'1', 'm', 'm2', 'V', 'A', 'F', 'H', 'Ohm', 'Ohm/sq', 'Hz', 's', 'K', 'degC', 'S',
         'deg'}


def qualified(package):
    for key, row in package.document('device.json')['items'].items():
        purpose = next((p for p in PURPOSES if row['use'][p] == 'allow'), None)
        if purpose:
            yield key, row, purpose


def callback_operand_gaps(cdf, selected):
    """Conditional-rule operands that callback execution cannot decide.

    Callbacks may recompute fixed/derived values, and the creation flow only
    verifies stability of requested writable inputs. Conditional rules are
    therefore decidable by the design consumer only when every operand is a
    writable input; anything else has no verified effective value.
    """
    if cdf['execution']['mode'] != 'cdf':
        return []
    return [name for name in sorted(operands(cdf, selected))
            if cdf['parameters'][name]['write'] not in {'allow', 'conditional'}]


def device_issues(package, key, row, purpose):
    def issue(name, path, reason):
        return {'device': key, 'file': name, 'path': path, 'reason': reason,
                'operation': purpose, 'action': 'pdk_generation_required'}
    yield from missing(package, key, row, purpose)
    if unknown(row['categories']):
        yield issue('device.json', pointer('items', key, 'categories'), 'category_not_defined')
    if row.get('limits'):
        yield issue('device.json', pointer('items', key, 'limits'), 'unsupported_voltage_limit_consumer')
    directory = row.get('dir')
    if not directory:
        return
    cdf_name = directory + '/cdf.json'
    cdf = package.document(cdf_name)
    selected = parameters(cdf)
    for name in selected:
        p = cdf['parameters'][name]
        if p['requirement'] == 'default' and unknown(p['default']):
            yield issue(cdf_name, pointer('parameters', name, 'default'), 'default_not_defined')
        if p['write'] in {'allow', 'conditional'} and isinstance(p['unit'], str) and p['unit'] not in UNITS:
            yield issue(cdf_name, pointer('parameters', name, 'unit'), 'unsupported_parameter_unit')
    for name in callback_operand_gaps(cdf, selected):
        yield issue(cdf_name, pointer('parameters', name), 'conditional_operand_not_writable')
    symbol_name = directory + '/symbol.json'
    if package.available(symbol_name):
        symbol = package.document(symbol_name)
        if symbol['parameterized'] is not False:
            yield issue(symbol_name, '/parameterized', 'unsupported_parameterized_symbol')
        if not unknown(symbol['grid']):
            grid = symbol['grid']
            for terminal, record in symbol['terminals'].items():
                if not unknown(record['anchors']) and any(
                        (Decimal(str(a['xy'][i])) - Decimal(str(grid['origin'][i])))
                        % Decimal(str(grid['step'][i])) for a in record['anchors'] for i in (0, 1)):
                    yield issue(symbol_name, pointer('terminals', terminal), 'anchor_off_connection_grid')
    name = directory + '/simulation.json'
    if not package.available(name):
        yield issue(name, '', 'simulation_interface_missing')
        return
    simulation = package.document(name)
    if not simulation['interfaces']:
        # A cell that contributes no netlist element (a global marker such as
        # gnd) is accepted only with its recorded simulation-run evidence.
        # Anything else stays a missing interface instead of a silent pass.
        if not netlist_invisible_basis(package, simulation):
            yield issue(name, '/interfaces', 'simulation_interface_missing')
        return
    if not any(package.manifest['dependencies'][d]['kind'] == 'interface' for d in simulation['depends_on']):
        yield issue(name, '/depends_on', 'netlist_source_dependency_missing')
    if not simulation.get('setup'):
        yield issue(name, '/setup', 'simulation_setup_missing')
    elif option_problems(simulation['setup']['options']):
        # Only options the recipe consumer can apply (registered in
        # simulation_schema) pass; anything else stays fail-closed so a PDK
        # setting is never dropped to satisfy publication.
        yield issue(name, '/setup/options', 'unsupported_simulator_options')
    interfaces = [i for i in simulation['interfaces'].values() if i['simulator'] == 'spectre']
    if not interfaces:
        yield issue(name, '/interfaces', 'spectre_interface_missing')
    if len(interfaces) > 1:
        yield issue(name, '/interfaces', 'ambiguous_spectre_interface')
    for interface in interfaces:
        for net, value in (interface.get('implicit_terminals') or {}).items():
            # A supported implicit terminal names the connection basis: either a
            # same-instance netlist port or a confirmed global net name.
            if not isinstance(value, str) or not value:
                yield issue(name, pointer('interfaces', interface['simulator'],
                                          'implicit_terminals', net),
                            'netlist_implicit_terminal_invalid')
        for net, mapping in interface['parameters'].items():
            path = pointer('interfaces', 'spectre', 'parameters', net)
            if unknown(mapping):
                yield issue(name, path, 'netlist_parameter_mapping_missing')
            elif 'derived' in mapping:
                # A derived mapping is consumable only through a registered
                # capability; iPar-synchronized instance parameters are the
                # confirmed case, anything else stays flagged.
                if mapping.get('implementation_ref') != IPAR_CAPABILITY:
                    yield issue(name, path, 'unsupported_derived_netlist_parameter')
            elif 'cdf' in mapping:
                p = cdf['parameters'].get(mapping['cdf'])
                if p is None or unknown(p['type']):
                    yield issue(name, path, 'netlist_parameter_outside_design_interface')
                elif p['write'] in {'allow', 'conditional'} or p['requirement'] in {'explicit', 'conditional'}:
                    # A design-controlled input needs a known unit for the
                    # scale checks a consumer may apply.
                    if unknown(p['unit']):
                        yield issue(name, path, 'netlist_parameter_outside_design_interface')
                elif unknown(p['default']):
                    # A fixed field passes through as-is; only its confirmed
                    # literal value can netlist.
                    yield issue(name, path, 'fixed_netlist_parameter_value_missing')
                elif 'scale' in mapping and unknown(p['unit']):
                    yield issue(name, path, 'netlist_parameter_outside_design_interface')
        if interface['model_source'] != 'external':
            continue
        model = package.document('model.json') if package.available('model.json') else {'corners': {}, 'configs': {}}
        configs = set(interface['model_refs'])
        if not any(configs <= set(c['configs']) for c in model['corners'].values()):
            yield issue('model.json', '/corners', 'model_corner_missing')
        resources = package.document('file.json')['items']
        for ref in configs:
            config = model['configs'].get(ref)
            if not config or config['simulator'] != interface['simulator']:
                yield issue('model.json', pointer('configs', ref), 'model_simulator_mismatch')
                continue
            for include in config['includes']:
                if not resources[include['file_ref']].get('dependency'):
                    yield issue('file.json', pointer('items', include['file_ref']), 'model_dependency_missing')


def netlist_invisible_basis(package, simulation):
    """True when the recorded evidence proves the cell emits no element.

    The evidence under /interfaces must resolve to recorded simulation runs;
    an interface-free document without such a basis is never accepted.
    """
    sources = package.document('sources.json')['items']
    refs = [source for path, source in simulation['evidence'].items()
            if path == '/interfaces' or path.startswith('/interfaces/')]
    return bool(refs) and all(sources.get(ref, {}).get('kind') == 'simulation' for ref in refs)


def issues(package):
    for key, row, purpose in qualified(package):
        yield from device_issues(package, key, row, purpose)


def accept(package):
    count = 0
    for gap in issues(package):
        count += 1
    if count:
        from .blockers import message

        fail(message(package), 'pdk_generation_incomplete')
    count = sum(1 for _ in qualified(package))
    if not count:
        from .blockers import message

        fail(message(package), 'pdk_generation_incomplete')
    from .resource_check import check
    for ref, dependency in package.manifest['dependencies'].items():
        if dependency['kind'] in {'file', 'model'}:
            check(package, ref)
    return {'feature': FEATURE, 'profile': PROFILE, 'device_count': count}

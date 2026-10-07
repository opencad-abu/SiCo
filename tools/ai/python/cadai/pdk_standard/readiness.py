"""Generation acceptance for the declared circuit and testbench device set."""

from decimal import Decimal

from .constraints import unknown
from .interface import parameters
from .jsonio import fail
from .patches import pointer
from .query import missing
from .publication import FEATURE, PROFILE

PURPOSES = ('circuit', 'testbench')
UNITS = {'1', 'm', 'm2', 'V', 'A', 'F', 'H', 'Ohm', 'Ohm/sq', 'Hz', 's', 'K', 'degC', 'S'}


def qualified(package):
    for key, row in package.document('device.json')['items'].items():
        purpose = next((p for p in PURPOSES if row['use'][p] == 'allow'), None)
        if purpose:
            yield key, row, purpose


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
    if cdf['execution']['mode'] == 'cdf' and (cdf.get('rules') or any(
            cdf['parameters'][n]['write'] == 'conditional'
            or cdf['parameters'][n]['requirement'] == 'conditional'
            or cdf['parameters'][n]['domain']['kind'] == 'conditional' for n in selected)):
        yield issue(cdf_name, '/execution', 'unsupported_callback_constraints')
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
    if not any(package.manifest['dependencies'][d]['kind'] == 'interface' for d in simulation['depends_on']):
        yield issue(name, '/depends_on', 'netlist_source_dependency_missing')
    if not simulation.get('setup'):
        yield issue(name, '/setup', 'simulation_setup_missing')
    elif simulation['setup']['options']:
        yield issue(name, '/setup/options', 'unsupported_simulator_options')
    interfaces = [i for i in simulation['interfaces'].values() if i['simulator'] == 'spectre']
    if not interfaces:
        yield issue(name, '/interfaces', 'spectre_interface_missing')
    if len(interfaces) > 1:
        yield issue(name, '/interfaces', 'ambiguous_spectre_interface')
    for interface in interfaces:
        if interface.get('implicit_terminals'):
            yield issue(name, '/interfaces', 'unsupported_implicit_terminals')
        for net, mapping in interface['parameters'].items():
            path = pointer('interfaces', 'spectre', 'parameters', net)
            if unknown(mapping):
                yield issue(name, path, 'netlist_parameter_mapping_missing')
            elif 'derived' in mapping:
                yield issue(name, path, 'unsupported_derived_netlist_parameter')
            elif 'cdf' in mapping:
                p = cdf['parameters'][mapping['cdf']]
                if mapping['cdf'] not in selected or unknown(p['type']) or unknown(p['unit']):
                    yield issue(name, path, 'netlist_parameter_outside_design_interface')
                elif p['write'] == 'deny' and unknown(p['default']):
                    yield issue(name, path, 'fixed_netlist_parameter_value_missing')
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


def issues(package):
    for key, row, purpose in qualified(package):
        yield from device_issues(package, key, row, purpose)


def accept(package):
    count, sample = 0, []
    for gap in issues(package):
        count += 1
        if len(sample) < 5:
            sample.append(gap)
    if count:
        fail('PDK generation incomplete (%d items): %s' % (count, sample), 'pdk_generation_incomplete')
    count = sum(1 for _ in qualified(package))
    if not count:
        fail('Publication requires at least one usable design device', 'pdk_generation_incomplete')
    from .resource_check import check
    for ref, dependency in package.manifest['dependencies'].items():
        if dependency['kind'] in {'file', 'model'}:
            check(package, ref)
    return {'feature': FEATURE, 'profile': PROFILE, 'device_count': count}

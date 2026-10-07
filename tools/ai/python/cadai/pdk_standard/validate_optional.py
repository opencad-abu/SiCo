"""Optional model configurations and netlist interface structures."""

from .constraints import numeric, unknown
from .jsonio import fail
from .validate_package import text


def model(value):
    from .validate import fields, envelope, identifier
    fields(value, ('source', 'depends_on', 'configs', 'corners'), ('evidence',))
    envelope(value)
    for key, row in value['configs'].items():
        identifier(key)
        fields(row, ('simulator', 'language', 'includes'), ('note',))
        text(row['simulator'])
        text(row['language'])
        if not isinstance(row['includes'], list) or not row['includes']:
            fail('Model configuration requires ordered includes')
        for include in row['includes']:
            fields(include, ('file_ref',), ('section',))
            identifier(include['file_ref'])
            if 'section' in include:
                text(include['section'])
    for key, row in value['corners'].items():
        identifier(key)
        fields(row, ('name', 'configs'), ('note',))
        text(row['name'])
        if not isinstance(row['configs'], list) or not row['configs'] or set(row['configs']) - set(value['configs']):
            fail('Corner requires existing model configurations')
        environments = {(value['configs'][ref]['simulator'], value['configs'][ref]['language']) for ref in row['configs']}
        if len(environments) != 1:
            fail('Corner configurations have incompatible simulators/languages')


def simulation(value):
    from .validate import fields, envelope, identifier
    fields(value, ('source', 'depends_on', 'interfaces'), ('evidence', 'setup'))
    if 'setup' in value:
        setup = value['setup']
        fields(setup, ('switch_views', 'stop_views', 'options'))
        for field in ('switch_views', 'stop_views'):
            if not isinstance(setup[field], list) or not setup[field]:
                fail('Simulation setup requires explicit view lists')
            for view in setup[field]:
                text(view)
        if not isinstance(setup['options'], dict):
            fail('Simulation options must be an explicit mapping')
        for key, item in setup['options'].items():
            text(key)
            if not isinstance(item, (str, bool)) and not numeric(item):
                fail('Simulation option must be a scalar')
    envelope(value)
    for key, row in value['interfaces'].items():
        identifier(key)
        fields(row, ('simulator', 'kind', 'name', 'model_source', 'term_order', 'terminal_map', 'parameters'),
               ('model_refs', 'implicit_terminals'))
        text(row['simulator'])
        text(row['name'])
        if row['kind'] not in {'primitive', 'model', 'subckt'} or row['model_source'] not in {'builtin', 'external'}:
            fail('Invalid simulator interface kind')
        if row['model_source'] == 'external' and not row.get('model_refs'):
            fail('External model requires model configuration references')
        order = row['term_order']
        if not isinstance(order, list) or len(order) != len(set(order)):
            fail('Invalid terminal order')
        for name in order:
            text(name)
        mapped = list(row['terminal_map'].values()) + list(row.get('implicit_terminals', {}))
        if len(mapped) != len(set(mapped)) or set(mapped) != set(order):
            fail('Terminal map must cover netlist terminals exactly once')
        for mapping in row['parameters'].values():
            if unknown(mapping):
                continue
            if 'cdf' in mapping:
                fields(mapping, ('cdf',), ('scale', 'unit'))
                text(mapping['cdf'])
                if 'scale' in mapping and not numeric(mapping['scale']):
                    fail('Invalid parameter scale')
            elif 'constant' in mapping:
                fields(mapping, ('constant', 'unit'))
                text(mapping['unit'])
            else:
                fields(mapping, ('derived', 'implementation_ref'))
                text(mapping['derived'])
                text(mapping['implementation_ref'])


def properties(value):
    from .validate import fields, envelope, identifier
    fields(value, ('source', 'depends_on', 'items'), ('evidence',))
    envelope(value)
    for key, row in value['items'].items():
        identifier(key)
        fields(row, ('name', 'value', 'unit', 'conditions', 'method'), ('definition',))
        text(row['name'])
        text(row['unit'])
        if not numeric(row['value']) or row['method'] not in {'documented', 'model_parameter', 'simulated'}:
            fail('Invalid physical property')
        if not isinstance(row['conditions'], dict):
            fail('Property conditions must be explicit')

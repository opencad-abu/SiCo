"""Design consumers enforce the effective confirmed rules, never caller assertions."""

from decimal import Decimal

from .constraints import accepts, condition, unknown
from .jsonio import fail
from .project import NUMBER, SCALES
from .source_check import observed
from .provenance import verify
from .readiness import device_issues
from .workspace import select
from .publication import require as require_publication


def obtain(service, device, purpose='circuit', *, live=True, verify_content=True):
    """Resolve the confirmed rule for one device.

    ``live=False`` skips the captured-source comparison so a caller (bind
    preflight) can check cheap published confirmations against a light
    identity stub; ``verify_content=False`` similarly skips rehashing every
    package file for that fail-fast check. The full bind read keeps both
    defaults as the authority.
    """
    package = select(service.workspace, device['target']['library'], service.environment)
    if verify_content:
        verify(package)
    require_publication(package)
    rows = package.document('device.json')['items']
    selected = [(key, row) for key, row in rows.items() if row['cell'] == device['target']['cell']
                and row['view'] == device['target']['view']
                and package.manifest['libraries'][row['library']]['name'] == device['target']['library']]
    if len(selected) != 1:
        fail('Device identity is absent or ambiguous in standard package', 'device_unavailable')
    key, row = selected[0]
    if row['use'][purpose] == 'deny':
        fail('Device is not permitted for ' + purpose, 'pdk_usage_denied')
    if row['use'][purpose] != 'allow':
        fail('Device use is ' + row['use'][purpose] + '; PDK generation is incomplete. '
             'Complete and publish the PDK package in the generation workflow; '
             'normal design only consumes published rules', 'pdk_generation_incomplete')
    gaps = list(device_issues(package, key, row, purpose))
    if gaps:
        fail('Required PDK facts missing from the published design interface: ' + str(gaps[:3])
             + '; complete PDK generation before design', 'pdk_generation_incomplete')
    cdf = package.document(row['dir'] + '/cdf.json')
    symbol = package.document(row['dir'] + '/symbol.json')
    simulation = package.document(row['dir'] + '/simulation.json')
    if live:
        observed(package, key, device, {*cdf['depends_on'], *symbol['depends_on'], *simulation['depends_on']})
    from .resource_check import check as check_resource
    resources = package.document('file.json')['items']
    model = package.document('model.json') if package.available('model.json') else {'configs': {}}
    for interface in simulation['interfaces'].values():
        for ref in interface.get('model_refs', []):
            for include in model['configs'][ref]['includes']:
                check_resource(package, resources[include['file_ref']]['dependency'])
    return {'revision': package.revision, 'device': key, 'cdf': cdf, 'symbol': symbol, 'simulation': simulation, 'use': purpose}


def spectre(rule):
    """The published spectre interface of a bound device; refuse instead of crashing.

    A published rule may carry no spectre interface (readiness reports
    ``spectre_interface_missing``); binding such a device for simulation must
    fail as a policy gap, never as an unhandled StopIteration.
    """
    interfaces = [i for i in rule['simulation']['interfaces'].values() if i['simulator'] == 'spectre']
    if not interfaces:
        fail('No published spectre interface for this device '
             '(readiness: spectre_interface_missing); bind a simulation-capable device '
             'or republish the PDK data', 'pdk_policy_mismatch')
    return interfaces[0]


def contract(rule, args):
    """Cheap caller confirmations that need only the published rule.

    Split from ``bind`` so a session can fail fast before any live detail
    read: the required writable set and callback order are published facts,
    and a wrong attempt must not pay minutes of bridge work first.
    """
    cdf = rule['cdf']
    interface = spectre(rule)
    if args.get('netlist_terminal_map', interface['terminal_map']) != interface['terminal_map']:
        fail('Netlist terminal mapping differs from published PDK', 'pdk_policy_mismatch')
    allowed = {n for n, p in cdf['parameters'].items() if p['write'] in {'allow', 'conditional'}}
    supplied = {p['name'] for p in args['writable_parameters']}
    if supplied != allowed:
        fail('Writable parameters must match the confirmed standard set: '
             + ', '.join(sorted(allowed))
             + '；以 get_pdk_data(section=\'cdf\') 的 write 标记为准', 'pdk_policy_mismatch')
    if cdf['execution']['mode'] == 'cdf' and args.get('callback_order') != cdf['execution']['order']:
        fail('Callback order differs from confirmed CDF execution'
             '；execution.order 以 get_pdk_data(section=\'cdf\') 为准', 'pdk_policy_mismatch')
    if args.get('extension_ref') and args['extension_ref'] != cdf['execution'].get('implementation_ref', 'builtin:cdf-callbacks:v1'):
        fail('Execution implementation differs from confirmed CDF rules', 'pdk_policy_mismatch')


def bind(service, device, args):
    rule = obtain(service, device, args.get('use', 'circuit'))
    contract(rule, args)
    if rule['cdf']['execution']['mode'] == 'literal' and any(
            c['presence'] != 'absent' for c in device['callbacks']['items']):
        fail('Literal policy conflicts with observed callbacks', 'pdk_policy_mismatch')
    return rule


def geometry(rule, body, pins):
    expected = rule['symbol']
    if body['occupied_bbox'] != expected['bbox']:
        fail('Live body bounds differ from confirmed symbol', 'pdk_source_changed')
    actual = {p['name']: p['anchors'] for p in pins}
    if actual != {n: p['anchors'] for n, p in expected['terminals'].items()}:
        fail('Pin anchors/escapes differ from confirmed symbol: '
             + anchor_difference(actual, expected['terminals']), 'pdk_policy_mismatch')


def anchor_difference(actual, expected, limit=2):
    """Name the first differing pins so a caller can correct the escapes once."""
    details = []
    for name in sorted(set(actual) | set(expected)):
        have = actual.get(name)
        want = expected[name]['anchors'] if name in expected else None
        if have == want:
            continue
        if have is None:
            details.append(f'terminal {name}: missing, expected {want}')
        elif want is None:
            details.append(f'terminal {name}: unexpected, observed {have}')
        else:
            for index in range(max(len(have), len(want))):
                observed = have[index] if index < len(have) else None
                wanted = want[index] if index < len(want) else None
                if observed != wanted:
                    details.append(
                        f'terminal {name} pin {index}: observed {observed} expected {wanted}')
                    break
        if len(details) >= limit:
            break
    return '; '.join(details) or 'structure differs'


def value(raw, parameter):
    typ = parameter['type']
    if typ in {'number', 'integer'} and isinstance(raw, str):
        match = NUMBER.fullmatch(raw.strip())
        if not match:
            fail('Nonliteral numeric CDF input is unsupported')
        raw = float(Decimal(match[1]) * Decimal(str(SCALES[match[2].lower()])))
        if typ == 'integer' and raw.is_integer():
            raw = int(raw)
    return raw


def values(rule, assignments, *, purpose=None):
    if purpose and purpose != rule['use']:
        fail('Device bound for a different use; bind again', 'pdk_policy_mismatch')
    cdf, resolved = rule['cdf'], {}
    params = cdf['parameters']
    if set(assignments) - set(params):
        fail('Unknown CDF input')
    for name, p in params.items():
        if not unknown(p['default']):
            resolved[name] = p['default']
        if name in assignments:
            resolved[name] = value(assignments[name], p)
    for name, p in params.items():
        writable = p['write'] == 'allow' or (p['write'] == 'conditional' and
                    condition(p['write_when'], params, resolved) is True)
        if name in assignments and not writable:
            fail('CDF write is denied or condition unresolved: ' + name, 'pdk_policy_mismatch')
        requirement = p['requirement']
        if requirement == 'conditional':
            required = condition(p['required_when'], params, resolved)
            if required is None:
                fail('CDF assignment condition unresolved: ' + name)
            requirement = 'explicit' if required else 'default'
        if requirement == 'explicit' and name not in assignments:
            fail('Explicit CDF decision required: ' + name, 'pdk_parameter_value_required')
        if p['write'] in {'allow', 'conditional'} and writable:
            if name not in resolved or accepts(resolved[name], p, {'definitions': params, 'values': resolved}) is not True:
                fail('CDF value violates or lacks complete constraints: ' + name, 'pdk_constraint_violation')
    for row in cdf.get('rules', {}).values():
        applicable = condition(row['when'], params, resolved) if 'when' in row else True
        if applicable is None or applicable and condition(row['check'], params, resolved) is not True:
            fail('CDF joint rule failed or unresolved: ' + row['message'], 'pdk_constraint_violation')
    return resolved


def current(service, record):
    rule = record.get('standard_rule')
    if rule:
        package = select(service.workspace, record['master']['target']['library'], service.environment)
        verify(package)
        if package.revision != rule['revision']:
            fail('PDK rules changed since binding; bind again', 'pdk_update_conflict')

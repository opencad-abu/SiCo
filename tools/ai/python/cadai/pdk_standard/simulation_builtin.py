"""Project captured simInfo into standard builtin simulation interfaces.

A builtin element is supplied by the simulator itself: there are no model
decks and no corners, so the deck projection does not apply. The netlist
identity is still evidence-bound. The captured simInfo supplies the emitted
primitive designation, the terminal order and the parameter mapping; the
device's confirmed inputs supply what a design may set; and a recorded
builtin probe run supplies the netlist line that the projection must agree
with, so the interface is never inferred from the symbol or a name table.
Only confirmed design inputs and fixed non-empty string parameters (for
example a source type token such as dc or pulse) are transmitted; every
other parameter keeps the simulator's own default, as observed in the probe.

The probe file stays private evidence: the package sources reference it.
"""

import json
import re
from pathlib import Path

from .jsonio import fail, sha
from .simulation_interfaces import MODEL_PORTS, TARGETS, pairs, unwrap

METHOD = 'sico-siminfo-builtin-v1'
PROBE_SCHEMA = 'sico.pdk.builtin-probe.v1'
LIMIT = 64 * 1024


def load(path):
    """Parse and bound one recorded builtin netlist probe file."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        fail('Builtin probe evidence is unreadable: ' + str(exc), 'pdk_source_unavailable')
    if len(raw) > LIMIT:
        fail('Builtin probe evidence exceeds its bound')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        fail('Builtin probe evidence is not JSON: ' + str(exc))
    if not isinstance(value, dict) or value.get('schema_version') != PROBE_SCHEMA:
        fail('Unsupported builtin probe evidence')
    runs, devices, setup = value.get('runs'), value.get('devices'), value.get('setup')
    if not isinstance(runs, dict) or not isinstance(devices, dict) or not isinstance(setup, dict):
        fail('Builtin probe evidence lacks runs/devices/setup')
    for simulator in TARGETS:
        row = runs.get(simulator)
        if (not isinstance(row, dict) or not isinstance(row.get('version'), str)
                or not row['version'] or not isinstance(row.get('run_ref'), str)
                or not row['run_ref']):
            fail('Builtin probe run is incomplete: ' + simulator)
    views = (setup.get('switch_views'), setup.get('stop_views'))
    if (any(not isinstance(v, list) or not v or any(not isinstance(n, str) or not n for n in v)
            for v in views) or not isinstance(setup.get('options'), dict)):
        fail('Builtin probe setup is incomplete')
    for cell, rows in devices.items():
        if not isinstance(cell, str) or not isinstance(rows, dict) or not rows:
            fail('Builtin probe device row is invalid')
        for simulator, row in rows.items():
            if (simulator not in TARGETS or not isinstance(row, dict)
                    or set(row) - {'netlist', 'element'}):
                fail('Builtin probe device row is invalid')
            if row.get('element') is False:
                continue
            if not isinstance(row.get('netlist'), str) or not row['netlist'].strip():
                fail(f'Builtin probe netlist line is missing: {cell}/{simulator}')
    digest = sha(raw)
    sources = {simulator: 'builtin_' + simulator + '_' + digest[7:15] for simulator in TARGETS}
    return {'path': str(target), 'digest': digest, 'sources': sources,
            'runs': runs, 'setup': setup, 'devices': devices}


def netlist_name(simulator, raw):
    """The primitive designation this simulator emits, or None when unrecorded.

    spectre writes the primitive token between the node list and the
    parameters (``R1 (1 2) resistor r=1k``). hspiceD identifies the element
    by the instance-name letter prefix (``r1 1 2 1k``) and never emits the
    CDL-side device class, so the fixture prefix is the netlist identity.
    """
    value = unwrap(raw.get('namePrefix') if simulator == 'hspiceD' else raw.get('componentName'))
    return value if isinstance(value, str) and value else None


def check_line(simulator, name, order, line):
    """The projected identity and terminals must be visible in the recorded line."""
    if simulator == 'hspiceD':
        fields = line.split()
        if not fields or not fields[0].lower().startswith(name.lower()):
            fail(f'Builtin probe conflicts with the emitted element: {name} ({line})')
        if len(fields) < 1 + len(order):
            fail('Builtin probe line lacks terminals: ' + line)
        return
    nodes = re.search(r'\(([^()]*)\)', line)
    if not nodes or len(nodes.group(1).split()) != len(order):
        fail('Builtin probe line lacks terminals: ' + line)
    if not re.search(f'(^|\\s){re.escape(name)}(\\s|$)', line):
        fail(f'Builtin probe conflicts with the emitted primitive: {name} ({line})')


def transmitted(simulator, raw, cdf):
    """(parameters, fixed) for one simulator from the device's confirmed inputs.

    ``fixed`` lists the netlist names that carry a fixed non-empty string
    parameter (a source type token, for example); blank or numeric fixed
    values keep the simulator default and are not transmitted.
    """
    props = unwrap(raw.get('propMapping'))
    props = dict(pairs(props)) if props else {}
    inst = unwrap(raw.get('instParameters'))
    if not isinstance(inst, list) or any(not isinstance(p, str) or not p for p in inst):
        fail('Invalid instance parameter list for ' + simulator)
    confirmed = set(cdf.get('interface_parameters') or [])
    parameters, fixed = {}, {}
    for net in inst:
        source = props.get(net, net)
        parameter = cdf['parameters'].get(source)
        if parameter is None:
            continue
        if source in confirmed and parameter['write'] in {'allow', 'conditional'}:
            parameters[net] = {'cdf': source}
        elif (parameter['write'] not in {'allow', 'conditional'}
              and isinstance(parameter.get('default'), str) and parameter['default'].strip()):
            parameters[net] = {'cdf': source}
            fixed[net] = source
    absent = confirmed - {row['cdf'] for row in parameters.values()}
    if absent:
        fail('Builtin interface misses confirmed inputs: ' + ', '.join(sorted(absent)))
    return parameters, fixed


def project(items, cdf, observed, terminals):
    """(interfaces, missing) for every target simulator the simInfo declares.

    observed holds the probe's netlist lines for this device; a simulator
    without a usable line stays a reported gap instead of an interface.
    """
    by_name = {}
    for item in items or []:
        if item.get('name') in TARGETS:
            by_name[item['name']] = unwrap(item.get('raw') or {})
    missing = [simulator for simulator in TARGETS if simulator not in by_name]
    interfaces = {}
    for simulator, raw in by_name.items():
        row = (observed or {}).get(simulator) or {}
        if row.get('element') is False:
            fail('SimInfo declares an element the probe did not observe: ' + simulator)
        line = row.get('netlist')
        if not isinstance(line, str) or not line.strip():
            missing.append(simulator + ':netlist')
            continue
        name = netlist_name(simulator, raw)
        if name is None:
            missing.append(simulator + ':name')
            continue
        order = unwrap(raw.get('termOrder'))
        if (not isinstance(order, list) or len(order) != len(MODEL_PORTS)
                or len(set(order)) != len(order)
                or any(not isinstance(t, str) or not t for t in order)
                or set(order) != set(terminals)):
            fail('Builtin terminal order does not match the symbol for ' + simulator)
        check_line(simulator, name, order, line)
        parameters, fixed = transmitted(simulator, raw, cdf)
        for net, source in fixed.items():
            if simulator != 'spectre':
                fail('Fixed builtin token is not verified for ' + simulator + ': ' + net)
            token = f"{net}={cdf['parameters'][source]['default']}"
            if token not in line:
                fail('Fixed builtin token was not observed: ' + token)
        interfaces[simulator] = {
            'kind': 'primitive',
            'model_source': 'builtin',
            'name': name,
            'parameters': parameters,
            'simulator': simulator,
            'term_order': list(MODEL_PORTS),
            'terminal_map': {terminal: MODEL_PORTS[position]
                             for position, terminal in enumerate(order)},
        }
    return interfaces, missing

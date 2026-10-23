"""Project captured CDF simInfo into standard simulation interfaces.

The projection is evidence-bound: the netlist terminal names and their order
come from the model decks' subckt headers for each simulator, and the CDF
simInfo termOrder gives the symbol terminal that sits at each position. The
instance parameters keep the CDF parameter a netlist name maps to, with
propMapping pairs as the only overrides. Anything the raw data does not prove
is reported instead of guessed.
"""

import re

from .jsonio import fail

TARGETS = ('hspiceD', 'spectre')
# User-confirmed convention (2026-10-10): two-terminal primitive/model elements
# use the letter roles "a" and "b" as their netlist port names, keeping the
# vocabulary uniform with the deck header names of every other device family.
MODEL_PORTS = ['a', 'b']
# Registered element-role vocabularies for built-in model cards.  These are
# simulator syntaxes, not vendor guesses: Spectre and HSPICE both order MOS
# model-card terminals d/g/s/b and BJT model-card terminals c/b/e[/s].
MODEL_ROLES = {
    'nmos': ['d', 'g', 's', 'b'],
    'pmos': ['d', 'g', 's', 'b'],
    'nmos4': ['d', 'g', 's', 'b'],
    'pmos4': ['d', 'g', 's', 'b'],
    'bsim': ['d', 'g', 's', 'b'],
    'bsim3': ['d', 'g', 's', 'b'],
    'bsim4': ['d', 'g', 's', 'b'],
    'npn': ['c', 'b', 'e', 's'],
    'pnp': ['c', 'b', 'e', 's'],
    'bjt': ['c', 'b', 'e', 's'],
    'd': MODEL_PORTS,
    'diode': MODEL_PORTS,
    'r': MODEL_PORTS,
    'resistor': MODEL_PORTS,
    'c': MODEL_PORTS,
    'capacitor': MODEL_PORTS,
    'l': MODEL_PORTS,
    'inductor': MODEL_PORTS,
}
# User-confirmed rule (2026-10-10): a CDF default of the form iPar(...) is not an
# editable input; the tooling synchronizes the value during netlisting, so the
# mapping records the registered synchronization capability instead of a cdf ref.
IPAR_CAPABILITY = 'builtin:ipar-sync:v1'


def unwrap(value):
    """Typed simInfo envelopes collapse to their plain value."""
    if isinstance(value, dict) and set(value) == {'type', 'status', 'value'}:
        return unwrap(value['value'])
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


def pairs(rows):
    """simInfo pair lists start with a nil placeholder, then alternating keys."""
    if rows is None or rows == []:
        return []
    if not isinstance(rows, list) or rows[0] is not None:
        fail('Invalid simInfo pair list')
    body = rows[1:]
    if len(body) % 2:
        fail('Invalid simInfo pair list')
    return list(zip(body[0::2], body[1::2]))


def tie_pin(value, order):
    """The pin a termMapping expression follows, or None when positional/plain."""
    if not isinstance(value, str):
        return None
    match = re.search(r'mappedRoot\("([^"]+)"\)', value)
    if not match:
        return None
    pin = match.group(1).lstrip('^').split(',')[0]
    for candidate in order:
        if candidate.casefold() == pin.casefold():
            return candidate
    return None


def global_net(value):
    """The explicitly global net a termMapping expression names, or None."""
    if not isinstance(value, str):
        return None
    match = re.search(r'mappedRoot\("\^([^",]+)', value)
    return match.group(1) if match else None


def model_ports(kind, model_type, count):
    """Netlist terminal roles for a registered model/primitive element type."""
    roles = MODEL_ROLES.get(str(model_type or '').casefold())
    if roles is not None and len(roles) >= count:
        return roles[:count]
    if kind == 'model' and count == len(MODEL_PORTS) and roles is None:
        # A two-terminal model card without a registered type keeps the
        # user-confirmed a/b convention; longer cards must name their type.
        return list(MODEL_PORTS)
    fail(f'No registered terminal convention for {kind} model type '
         f'{model_type!r} with {count} terminals')


def parameter_mapping(target, default):
    """The mapping record for one netlist parameter target and CDF default."""
    if isinstance(default, str) and default.strip().startswith('iPar('):
        return {'derived': f'tool-synchronized instance parameter ({default.strip()})',
                'implementation_ref': IPAR_CAPABILITY}
    return {'cdf': target}


def project(items, name, model_refs, decks, terminals=None, defaults=None):
    """Standard interfaces for every captured target simulator, plus gaps.

    model_refs maps each simulator to its corner configuration ids, and decks
    maps each simulator to the evidence the model decks provide:
    {'ports': [netlist names in header order], 'kind': 'subckt'|'model'}.
    When the symbol's terminals are supplied, deck ports without a symbol
    terminal become implicit terminals whose connection must be proven by a
    termMapping expression on another simulator.
    defaults maps CDF parameter names to their raw default expressions; an
    iPar(...) default marks a tool-synchronized parameter that is not editable.
    """
    by_name = {}
    for item in items or []:
        if item.get('name') in TARGETS:
            by_name[item['name']] = unwrap(item.get('raw') or {})
    missing = [simulator for simulator in TARGETS if simulator not in by_name]
    for simulator in by_name:
        if simulator not in decks:
            missing.append(simulator + ':deck')
    ties, globals_ = {}, {}
    if terminals is not None:
        for raw in by_name.values():
            order = unwrap(raw.get('termOrder'))
            mapping = dict(pairs(unwrap(raw.get('termMapping'))))
            for symbol in order or []:
                if symbol in terminals:
                    continue
                pin = tie_pin(mapping.get(symbol), order)
                if pin:
                    ties[symbol] = pin
                net = global_net(mapping.get(symbol))
                if net:
                    globals_[symbol] = net
    interfaces = {}
    for simulator, raw in by_name.items():
        if simulator not in decks:
            continue
        declared = decks[simulator]
        ports = declared.get('ports')
        kind = declared.get('kind')
        if kind not in {'subckt', 'model', 'primitive'}:
            fail('Unsupported deck kind for ' + simulator)
        order = unwrap(raw.get('termOrder'))
        if not isinstance(order, list) or not order or not all(isinstance(t, str) and t for t in order):
            fail('Invalid terminal order for ' + simulator)
        if kind in {'model', 'primitive'}:
            if len(order) != len(MODEL_PORTS):
                ports = model_ports(kind, declared.get('model_type'), len(order))
            else:
                ports = list(MODEL_PORTS)
        if not isinstance(ports, list) or len(ports) != len(order) or not all(
                isinstance(p, str) and p for p in ports) or len(set(ports)) != len(ports):
            fail('Deck ports do not match the terminal order for ' + simulator)
        mapping = dict(pairs(unwrap(raw.get('termMapping'))))
        if set(mapping) != set(order):
            fail('Terminal mapping does not cover the order for ' + simulator)
        props = dict(pairs(unwrap(raw.get('propMapping'))))
        parameters = unwrap(raw.get('instParameters'))
        if not isinstance(parameters, list) or not parameters or not all(
                isinstance(p, str) and p for p in parameters):
            fail('Invalid instance parameter list for ' + simulator)
        if terminals is None:
            terminal_map = {terminal: ports[i] for i, terminal in enumerate(order)}
            implicit = {}
        else:
            stray = set(terminals) - set(order)
            if stray:
                fail('Symbol terminal outside the deck order for ' + simulator + ': ' + ', '.join(sorted(stray)))
            terminal_map, implicit = {}, {}
            for position, symbol in enumerate(order):
                if symbol in terminals:
                    terminal_map[symbol] = ports[position]
                    continue
                pin = ties.get(symbol)
                if pin and pin in order and pin.casefold() != symbol.casefold():
                    implicit[ports[position]] = ports[order.index(pin)]
                    continue
                net = globals_.get(symbol)
                if net:
                    # The vendor simInfo explicitly marks this as a global net
                    # (mappedRoot("^name")); it is the connection basis.
                    implicit[ports[position]] = net
                    continue
                if not pin or pin not in order:
                    fail(f'Implicit terminal has no confirmed connection: '
                         f'{simulator}/{symbol}')
                fail(f'Implicit terminal maps to itself without a global-net marker: '
                     f'{simulator}/{symbol}')
        interfaces[simulator] = {
            'kind': kind,
            'model_refs': list((model_refs or {}).get(simulator) or []),
            'model_source': 'external',
            'name': name,
            'parameters': {p: parameter_mapping(props.get(p, p), (defaults or {}).get(props.get(p, p)))
                           for p in parameters},
            'simulator': simulator,
            'term_order': list(ports),
            'terminal_map': terminal_map,
            **({'implicit_terminals': implicit} if implicit else {}),
        }
    return interfaces, missing

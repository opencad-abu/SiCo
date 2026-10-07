"""Bounded gpdk OpAmp process-corner and load-parameter matrix."""
import math
from pathlib import Path

from .circuit_gate import qualify_gpdk, skill_value
from .circuit_recipe import NAME, REF, REQUEST
from .circuit_schema import tool, string_schema

CAPS = {'type': 'array', 'items': {'type': 'number', 'minimum': 1e-15, 'maximum': 1e-10},
        'minItems': 1, 'maxItems': 4, 'default': [1e-13, 2e-13]}
RES = {'type': 'array', 'items': {'type': 'number', 'minimum': 100, 'maximum': 1e6},
       'minItems': 1, 'maxItems': 4, 'default': [1000]}
ROOT = {'gpdk_root': string_schema(1024)}
MATRIX_TOOLS = [
    tool('create_opamp_matrix', 'Save a NEW Maestro cell in the same work library from a validated '
         'reference or rebuilt OpAmp setup. Configure tt/ff/ss at 27C and the Cartesian product of '
         'cload_values_f and rload_values_ohm. At most 16 load combinations, 96 test points. '
         'Inherit confirmed presentation, keep original setup unchanged. Does not run or resize devices.',
         {'setup_ref': REF, 'cell': NAME, **REQUEST, **ROOT,
          'cload_values_f': CAPS, 'rload_values_ohm': RES},
         ('setup_ref', 'cell', 'request_id', 'gpdk_root'), True),
    tool('inspect_opamp_matrix', 'Recheck saved matrix setup, corner model sections, sweep settings, '
         'source designs, rebuilt views/configs and foreground session. No save or repair.',
         {'setup_ref': REF}, ('setup_ref',)),
    tool('run_opamp_matrix', 'Start the validated tt/ff/ss x load matrix asynchronously. Retain exact '
         'history; use get_opamp_reference_status, stop_opamp_reference and release_opamp_reference '
         'for lifecycle. Reuse identical request_id after uncertain transport.',
         {'setup_ref': REF, **REQUEST, **ROOT}, ('setup_ref', 'request_id', 'gpdk_root'), True),
    tool('read_opamp_matrix_results', 'Read nine scalars for every exact history/test/corner/point/load '
         'combination. Report missing/non-scalar values explicitly and refuse unexpected matrix dimensions. '
         'Includes units and actual RDB load parameters. Does not infer spec qualification.',
         {'run_ref': REF}, ('run_ref',)),
]
MATRIX_NAMES = frozenset(t['name'] for t in MATRIX_TOOLS)


def checked_matrix(name, args):
    from .circuit_tools import CircuitArgumentError
    schema = next(t['inputSchema'] for t in MATRIX_TOOLS if t['name'] == name)
    if not isinstance(args, dict) or set(args) - set(schema['properties']) or set(schema['required']) - set(args):
        raise CircuitArgumentError('unsupported or missing matrix fields')
    import re
    out = {}
    for key, rule in schema['properties'].items():
        if key not in args and 'default' not in rule:
            continue
        value = args.get(key, rule.get('default'))
        if rule['type'] == 'array':
            if not isinstance(value, list) or not 1 <= len(value) <= 4:
                raise CircuitArgumentError(key + ' requires 1 to 4 values')
            item = rule['items']
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                   not math.isfinite(v) or not item['minimum'] <= v <= item['maximum'] for v in value):
                raise CircuitArgumentError('invalid ' + key)
            if any(abs(v - other) <= max(abs(v) * 1e-12, 1e-30)
                   for i, v in enumerate(value) for other in value[i + 1:]):
                raise CircuitArgumentError('duplicate or numerically indistinguishable ' + key)
            out[key] = list(value)
        else:
            if not isinstance(value, str) or not value or len(value) > rule['maxLength'] or (
                    'pattern' in rule and not re.fullmatch(rule['pattern'], value)):
                raise CircuitArgumentError('invalid ' + key)
            out[key] = value
    return out


def build_matrix_skill(name, args):
    from .circuit_tools import CircuitArgumentError
    a = checked_matrix(name, args)
    if 'gpdk_root' in a:
        root = Path(a['gpdk_root'])
        if not root.is_absolute():
            raise CircuitArgumentError('gpdk_root must be absolute')
        root = root.resolve()
        qualify_gpdk(root)
    if name == 'create_opamp_matrix':
        func, vals = 'aiMatrixCreate', [a['setup_ref'], a['request_id'], a['cell'], str(root),
                                      a['cload_values_f'], a['rload_values_ohm']]
    elif name == 'run_opamp_matrix':
        func, vals = 'aiMatrixRun', [a['setup_ref'], a['request_id'], str(root)]
    else:
        func = {'inspect_opamp_matrix': 'aiMatrixInspect', 'read_opamp_matrix_results': 'aiMatrixResults'}[name]
        vals = [a.get('setup_ref', a.get('run_ref'))]
    return func + '(' + ' '.join(skill_value(v) for v in vals) + ')'

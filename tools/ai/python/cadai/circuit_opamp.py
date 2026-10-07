"""Fixed gpdk045 RAK reference recovery, prior to from-scratch OpAmp generation."""
from pathlib import Path

from .circuit_gate import qualify_gpdk, skill_value
from .circuit_recipe import NAME, REF, REQUEST
from .circuit_schema import string_schema, tool

ROOT = {'gpdk_root': string_schema(1024)}
SETUP = {'setup_ref': REF}
RUN = {'run_ref': REF}
OPAMP_TOOLS = [
    tool('create_opamp_reference_maestro', 'Save a NEW Maestro work copy of the supplied gpdk045 v3.5 '
         'Two_Stage_Opamp/OpAmp/maestro_basic reference. Keep AC/DC and TRAN/DC/STB, mc section and '
         'reference outputs/specs; only repair model/include paths. Source designs remain read-only. '
         'Requires existing work library and confirmed task mode. Does not create circuits or run.',
         {'library': NAME, 'cell': NAME, 'task_ref': REF, **REQUEST, **ROOT},
         ('library', 'cell', 'task_ref', 'request_id', 'gpdk_root'), True),
    tool('inspect_opamp_reference_maestro', 'Revalidate the retained work setup, source designs and '
         'foreground session. Does not save user changes or change focus.', SETUP, ('setup_ref',)),
    tool('run_opamp_reference', 'Recheck the qualified PDK files, setup and source snapshots and '
         'start one asynchronous nominal AC+TRAN reference run. Reuse identical request_id after '
         'uncertain transport. Retain the exact history; uses the copied reference job policy.',
         {**SETUP, **REQUEST, **ROOT}, ('setup_ref', 'request_id', 'gpdk_root'), True),
    tool('get_opamp_reference_status', 'Read exact reference history completion and simulation errors; '
         'completion does not establish spec pass.', RUN, ('run_ref',)),
    tool('read_opamp_reference_results', 'Read nine reference scalars by exact history/test/corner/point. '
         'Missing/non-scalar results are explicit. Units come from the fixed measurement recipe; '
         'spec qualification is not performed.', RUN, ('run_ref',)),
    tool('stop_opamp_reference', 'Request stop of this reference history only; poll afterward. '
         'Does not close GUI windows.', RUN, ('run_ref',), True),
    tool('release_opamp_reference', 'Detach from foreground setup without closing it, or close only '
         'the owned idle unmodified background session. Refuse unknown/active runs.', SETUP, ('setup_ref',), True),
]
OPAMP_NAMES = frozenset(t['name'] for t in OPAMP_TOOLS)


def build_opamp_skill(name, args):
    from .circuit_tools import CircuitArgumentError
    if 'gpdk_root' in args:
        root = Path(args['gpdk_root'])
        if not root.is_absolute():
            raise CircuitArgumentError('gpdk_root must be absolute')
        root = root.resolve()
        qualify_gpdk(root)
    if name == 'create_opamp_reference_maestro':
        if args['library'] in {'basic', 'analogLib', 'gpdk045', 'Two_Stage_Opamp'}:
            raise CircuitArgumentError('reference recovery requires a separate work library')
        source = root.parent/'libs/Two_Stage_Opamp'
        if not (source/'OpAmp/maestro_basic').is_dir():
            raise CircuitArgumentError('paired Two_Stage_Opamp RAK reference is missing')
        func = 'aiOpampCreate'
        vals = [args['task_ref'], args['request_id'], args['library'], args['cell'], str(root), str(source)]
    elif name == 'run_opamp_reference':
        func, vals = 'aiOpampRun', [args['setup_ref'], args['request_id'], str(root)]
    else:
        func = {'inspect_opamp_reference_maestro': 'aiOpampInspect',
                'get_opamp_reference_status': 'aiOpampStatus',
                'read_opamp_reference_results': 'aiOpampResults',
                'stop_opamp_reference': 'aiOpampStop',
                'release_opamp_reference': 'aiOpampRelease'}[name]
        vals = [args.get('setup_ref', args.get('run_ref'))]
    return func+'('+' '.join(skill_value(v) for v in vals)+')'

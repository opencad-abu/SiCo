"""Generic Maestro capture, bounded queries and declarative specification reports."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .circuit_schema import string_schema, tool
from .skill_result import call_skill
from .result_contract import (MAX_OUTPUTS, SCHEMA, REPORT_SCHEMA, ResultError, canonical, digest, evaluate,
                              normalize_capture, text, validate_contract)
from sicostate import absolute, project_directory, validate_directory

ID = string_schema(80, pattern=r'^(results|report)_[0-9a-f]{64}$')
COORD = {'type': 'object', 'properties': {'test': string_schema(), 'corner': string_schema(),
         'point': {'type': 'integer', 'minimum': 0}}, 'required': ['test', 'corner', 'point'],
         'additionalProperties': False}
SPEC = {'type': 'object', 'properties': {'test': string_schema(), 'output': string_schema(),
        'unit': string_schema(description='Unit of the stored numeric value, explicitly declared; no conversion.'),
        'lower': {'type': 'number'}, 'upper': {'type': 'number'},
        'lower_inclusive': {'type': 'boolean'}, 'upper_inclusive': {'type': 'boolean'}},
        'required': ['test', 'output', 'unit'], 'additionalProperties': False}
CONTRACT = {'type': 'object', 'properties': {'source': string_schema(1024),
            'expected_test_points': {'type': 'array', 'items': COORD, 'minItems': 1, 'maxItems': 2048},
            'specifications': {'type': 'array', 'items': SPEC, 'minItems': 1, 'maxItems': 64}},
            'required': ['source', 'expected_test_points', 'specifications'], 'additionalProperties': False}
RESULT_TOOLS = [
    tool('read_maestro_results', 'Capture point-expression results of one EXACT existing Maestro history, '
         'using an open_design_view context_ref. Circuit/PDK/test/corner names are unrestricted. '
         'Read only: no simulation, expression evaluation, save or setup changes. Returns an immutable '
         'workspace result_ref; units remain unknown until explicitly declared. v1 refuses reliability '
         'and unrepresented aggregate dimensions. Bounded to 2048 test points / 4096 expressions.',
         {'context_ref': string_schema(128), 'history': string_schema()}, ('context_ref', 'history')),
    tool('query_maestro_results', 'Page immutable results or report rows by exact test/output/corner/point. '
         'Coordinates and parameters come from the captured RDB, never current GUI selection.',
         {'result_ref': ID, 'entity': {'type': 'string', 'enum': ['outputs', 'tests', 'coordinates', 'definitions', 'rows'], 'default': 'outputs'},
          'test': string_schema(), 'output': string_schema(), 'corner': string_schema(),
          'point': {'type': 'integer', 'minimum': 0},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30},
          'offset': {'type': 'integer', 'minimum': 0, 'maximum': MAX_OUTPUTS, 'default': 0}}, ('result_ref',)),
    tool('evaluate_measurement_specs', 'Evaluate an immutable result_ref against an EXPLICIT contract: '
         'expected test/corner/point coordinates, named outputs, numeric-unit declarations, lower/upper '
         'bounds (inclusive by default). No test names, limits, unit conversion or expected coverage inferred. '
         'Report pass/fail/incomplete and each metric worst point; never claim full design qualification.',
         {'result_ref': ID, 'contract': CONTRACT}, ('result_ref', 'contract')),
]
RESULT_NAMES = frozenset(t['name'] for t in RESULT_TOOLS)
MAX_BYTES = 8 * 1024 * 1024
ARTIFACT_SCHEMAS = {'results': SCHEMA, 'report': REPORT_SCHEMA,
                    'waveform': 'cad.maestro.waveform.v1',
                    'wave_measurements': 'cad.waveform.measurements.v1',
                    'pair_measurements': 'cad.waveform.pair-measurements.v1',
                    'wave_specs': 'cad.waveform.spec-report.v1',
                    'ac_waveform': 'cad.maestro.ac-waveform.v1',
                    'ac_measurements': 'cad.ac.measurements.v1',
                    'ac_response': 'cad.ac.response-report.v1'}


def checked(name, args):
    definition = next(t for t in RESULT_TOOLS if t['name'] == name)
    schema = definition['inputSchema']
    if not isinstance(args, dict) or set(args) - set(schema['properties']) or set(schema['required']) - set(args):
        raise ResultError('invalid or missing result tool fields')
    out = copy.deepcopy(args)
    for key, rule in schema['properties'].items():
        if key not in out:
            if 'default' in rule: out[key] = rule['default']
            continue
        v = out[key]
        if key == 'contract': validate_contract(v)
        elif rule['type'] == 'string':
            text(v, rule.get('maxLength', 256))
            if 'pattern' in rule and not re.fullmatch(rule['pattern'], v): raise ResultError('invalid result reference')
            if 'enum' in rule and v not in rule['enum']: raise ResultError('invalid entity')
        elif type(v) is not int or v < rule['minimum'] or v > rule.get('maximum', 2**31 - 1):
            raise ResultError('invalid integer argument')
    return out


class ResultStore:
    """Only server-generated, content-addressed artifacts in this workspace."""
    def __init__(self, workspace):
        if workspace is None: raise ResultError('result tools require an explicit workspace')
        self.workspace = absolute(workspace)
        if not self.workspace.is_dir(): raise ResultError('result workspace is unavailable')

    def directory(self, *, create=True):
        try:
            path = project_directory(self.workspace, 'ai/measurement-results', create=create)
            if path.parent.is_symlink() or not path.parent.is_dir():
                raise ValueError('invalid result ancestor')
            validate_directory(path)
        except (OSError, ValueError) as exc:
            raise ResultError('result state root is unavailable') from exc
        return path

    def put(self, prefix, value):
        if prefix not in ARTIFACT_SCHEMAS or value.get('schema') != ARTIFACT_SCHEMAS[prefix]:
            raise ResultError('unsupported artifact schema/prefix')
        data = canonical(value)
        if len(data) > MAX_BYTES: raise ResultError('result artifact exceeds 8 MiB')
        ref = prefix + '_' + digest(value)
        directory = self.directory(create=True); path = directory / (ref + '.json')
        fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as f: f.write(data)
            try: os.link(temporary, path)
            except FileExistsError:
                self.get(ref)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return ref, {'path': str(path), 'sha256': digest(value), 'bytes': len(data)}

    def get(self, ref):
        match = re.fullmatch(r'(' + '|'.join(ARTIFACT_SCHEMAS) + r')_([0-9a-f]{64})', ref)
        if not match: raise ResultError('invalid result reference')
        path = self.directory(create=False) / (ref + '.json')
        if path.is_symlink() or not path.is_file(): raise ResultError('result artifact missing or invalid')
        if path.stat().st_size > MAX_BYTES: raise ResultError('result artifact exceeds limit')
        try:
            value = json.loads(path.read_bytes())
            if digest(value) != match[2]: raise ResultError('result artifact digest changed')
            schema = ARTIFACT_SCHEMAS[match[1]]
            if not isinstance(value, dict) or value.get('schema') != schema:
                raise ResultError('unsupported artifact schema')
        except (ValueError, TypeError, OverflowError) as exc:
            raise ResultError('invalid result artifact') from exc
        return value


def _native(client, fn, *args):
    code = fn + '(' + ' '.join(json.dumps(v, ensure_ascii=False) for v in args) + ')'
    ok, detail = call_skill(client, code, native=True)
    if not ok: raise ResultError(str(detail.get('message', detail.get('code', 'native read failed'))))
    return detail


def capture(client, context, history):
    capture_id = 'capture_' + uuid.uuid4().hex
    try:
        meta = _native(client, 'aiResultsCapture', context, history, capture_id)
        pages = meta.get('pages')
        if type(pages) is not int or not 1 <= pages <= 512 or meta.get('history') != history or meta.get('context_ref') != context:
            raise ResultError('invalid capture identity/pages')
        records = []
        for i in range(pages):
            page = _native(client, 'aiResultsPage', capture_id, i)
            batch = page.get('records')
            if not isinstance(batch, list) or len(records) + len(batch) > 8192:
                raise ResultError('invalid capture page')
            records.extend(batch)
        return normalize_capture(meta, records)
    finally:
        # This releases only private frozen pages, never a Virtuoso view/session.
        try: _native(client, 'aiResultsRelease', capture_id)
        except Exception: pass


def call_result_tool(name, args, *, workspace, client):
    a = checked(name, args)
    store = ResultStore(workspace)
    if name == 'read_maestro_results':
        value = capture(client, a['context_ref'], a['history'])
        ref, artifact = store.put('results', value)
        return dict(ok=True, result_ref=ref, artifact=artifact, target=value['target'], history=value['history'],
                    test_points=len(value['tests']), output_count=len(value['outputs']),
                    simulation_status=value['simulation_status'], expected_coverage_verified=False,
                    spec_qualified=None, scope=value['scope'], unit_source='not_exposed_by_rdb')
    value = store.get(a['result_ref'])
    if name == 'evaluate_measurement_specs':
        report = evaluate(value, a['contract'])
        ref, artifact = store.put('report', report)
        return dict(ok=True, report_ref=ref, artifact=artifact,
                    **{k: v for k, v in report.items() if k not in {'contract', 'rows', 'coordinates', 'schema'}})
    entity = a['entity']
    if entity not in value or entity not in {'outputs', 'tests', 'coordinates', 'definitions', 'rows'}:
        raise ResultError('entity not present in this artifact; use rows for a report')
    allowed = {'outputs': {'test', 'output', 'corner', 'point'}, 'rows': {'test', 'output', 'corner', 'point'},
               'tests': {'test', 'corner', 'point'}, 'coordinates': {'corner', 'point'},
               'definitions': {'test', 'output'}}[entity]
    filters = {k: a[k] for k in ('test', 'output', 'corner', 'point') if k in a}
    if set(filters) - allowed: raise ResultError('filter is not applicable to entity')
    selected = [r for r in value[entity] if all(r[k] == v for k, v in filters.items())]
    end = a['offset'] + a['limit']
    return {'ok': True, 'result_ref': a['result_ref'], 'entity': entity, 'total': len(selected),
            'items': selected[a['offset']:end], 'next_offset': end if end < len(selected) else None}

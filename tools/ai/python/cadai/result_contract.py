"""Circuit-independent scalar result and explicit acceptance contracts (v1)."""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA = 'cad.maestro.point-results.v1'
REPORT_SCHEMA = 'cad.measurement-report.v1'
MAX_TESTS = 2048
MAX_OUTPUTS = 4096


class ResultError(ValueError):
    pass


def text(value, limit=256):
    if (not isinstance(value, str) or not value or len(value) > limit or
            value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ResultError('expected bounded nonempty text without control characters')
    return value


def finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and abs(value) < 1e300
    except OverflowError:
        return False


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def coordinate(test, corner, point):
    text(test); text(corner)
    if type(point) is not int or point < 0:
        raise ResultError('point must be a nonnegative integer')
    return test, corner, point


def parameter(value, depth=0):
    if depth > 8:
        raise ResultError('parameter depth limit')
    if value is None or finite(value) or (isinstance(value, str) and len(value) <= 4096):
        return value
    if isinstance(value, list) and len(value) <= 128:
        return [parameter(v, depth + 1) for v in value]
    raise ResultError('unsupported parameter value')


def normalize_capture(meta, records):
    """Validate frozen native records without naming a circuit, test or corner."""
    if not isinstance(meta, dict) or meta.get('source') != 'maeReadResDB' or meta.get('scope') != 'point_expressions':
        raise ResultError('unexpected native capture contract')
    target = meta.get('target')
    if not isinstance(target, list) or len(target) != 3:
        raise ResultError('capture target missing')
    for v in target: text(v)
    text(meta.get('history'))
    text(meta.get('library_path'), 4096)
    if not isinstance(records, list) or len(records) > MAX_OUTPUTS + 2 * MAX_TESTS:
        raise ResultError('capture size limit')
    coords, tests, outputs, definitions = {}, {}, {}, {}
    for row in records:
        if not isinstance(row, list) or not row:
            raise ResultError('invalid native record')
        if row[0] == 'coordinate' and len(row) == 4:
            _, corner, point, params = row
            # The native SKILL serializer emits nil as JSON null, including the
            # empty parameter list of a nominal point without design variables.
            if params is None:
                params = []
            coordinate('_', corner, point)
            key = (corner, point)
            if key in coords or not isinstance(params, list) or len(params) > 128:
                raise ResultError('duplicate/invalid coordinate parameters')
            values = []
            seen = set()
            for p in params:
                if not isinstance(p, list) or len(p) != 3:
                    raise ResultError('invalid parameter record')
                name, kind, value = p
                text(name); text(kind)
                if name in seen: raise ResultError('duplicate parameter name')
                seen.add(name)
                values.append({'name': name, 'kind': kind, 'value': parameter(value)})
            coords[key] = {'corner': corner, 'point': point,
                           'parameters': sorted(values, key=lambda v: v['name'])}
        elif row[0] == 'test' and len(row) == 6:
            _, test, corner, point, status, info = row
            key = coordinate(test, corner, point)
            text(status)
            if info is not None and (not isinstance(info, str) or len(info) > 4096):
                raise ResultError('invalid test status description')
            if key in tests: raise ResultError('duplicate test point')
            tests[key] = dict(test=test, corner=corner, point=point, status=status, info=info)
        elif row[0] == 'definition' and len(row) == 5:
            _, test, output, eval_type, expression = row
            text(test); text(output)
            if eval_type != 'point' or not isinstance(expression, str) or len(expression) > 4096:
                raise ResultError('unsupported expression definition')
            if (test, output) in definitions: raise ResultError('duplicate output definition')
            definitions[test, output] = dict(test=test, output=output, evaluation=eval_type,
                                              expression=expression, source='history_setup')
        elif row[0] == 'output' and len(row) == 7:
            _, test, corner, point, output, status, value = row
            key = (*coordinate(test, corner, point), text(output))
            if key in outputs: raise ResultError('duplicate measurement coordinate')
            if status not in {'scalar', 'missing', 'error', 'non_scalar'}:
                raise ResultError('unknown measurement status')
            if (status == 'scalar' and not finite(value)) or (status != 'scalar' and value is not None):
                raise ResultError('inconsistent scalar status/value')
            outputs[key] = dict(test=test, corner=corner, point=point, output=output,
                                status=status, value=value, unit=None, unit_source='not_exposed_by_rdb')
        else:
            raise ResultError('unknown native record')
    if not tests or len(tests) > MAX_TESTS or len(outputs) > MAX_OUTPUTS:
        raise ResultError('empty/oversized test matrix')
    if len(tests) != meta.get('test_points') or len(outputs) != meta.get('outputs'):
        raise ResultError('truncated capture counts')
    if any(k[1:] not in coords for k in tests) or any(k[:3] not in tests for k in outputs):
        raise ResultError('orphan result coordinate')
    if set(coords) != {k[1:] for k in tests}:
        raise ResultError('coordinate without test')
    if any((k[0], k[3]) not in definitions for k in outputs):
        raise ResultError('result has no historical expression definition')
    if any(k[0] not in {t[0] for t in tests} for k in definitions):
        raise ResultError('definition has no test')
    return {'schema': SCHEMA, 'target': target, 'history': meta['history'], 'source': meta['source'],
            'library_path': meta['library_path'],
            'scope': meta['scope'], 'coordinates': [coords[k] for k in sorted(coords)],
            'tests': [tests[k] for k in sorted(tests)], 'outputs': [outputs[k] for k in sorted(outputs)],
            'definitions': [definitions[k] for k in sorted(definitions)],
            'simulation_status': 'done' if all(t['status'] == 'done' for t in tests.values()) else 'not_all_done',
            'expected_coverage_verified': False, 'spec_qualified': None}


def validate_contract(contract):
    if not isinstance(contract, dict) or set(contract) != {'source', 'expected_test_points', 'specifications'}:
        raise ResultError('contract requires source, expected_test_points and specifications')
    text(contract['source'], 1024)
    expected, specs = contract['expected_test_points'], contract['specifications']
    if not isinstance(expected, list) or not 1 <= len(expected) <= MAX_TESTS:
        raise ResultError('expected_test_points must contain 1..2048 explicit coordinates')
    keys = set()
    for entry in expected:
        if not isinstance(entry, dict) or set(entry) != {'test', 'corner', 'point'}:
            raise ResultError('expected coordinate requires test/corner/point')
        key = coordinate(entry['test'], entry['corner'], entry['point'])
        if key in keys: raise ResultError('duplicate expected coordinate')
        keys.add(key)
    if not isinstance(specs, list) or not 1 <= len(specs) <= 64:
        raise ResultError('specifications must contain 1..64 entries')
    names = set()
    for spec in specs:
        if (not isinstance(spec, dict) or not {'test', 'output', 'unit'} <= set(spec) or
                set(spec) - {'test', 'output', 'unit', 'lower', 'upper', 'lower_inclusive', 'upper_inclusive'}):
            raise ResultError('invalid specification fields')
        for field in ('test', 'output', 'unit'): text(spec[field])
        key = (spec['test'], spec['output'])
        if key in names: raise ResultError('duplicate specification')
        names.add(key)
        if not any(k[0] == spec['test'] for k in keys):
            raise ResultError('spec test has no expected coordinates')
        if not {'lower', 'upper'} & set(spec): raise ResultError('at least one numeric bound required')
        for side in ('lower', 'upper'):
            if side in spec and not finite(spec[side]): raise ResultError('bound must be finite')
            if side + '_inclusive' in spec:
                if side not in spec or type(spec[side + '_inclusive']) is not bool:
                    raise ResultError('inclusive flag requires its bound and a boolean')
        if 'lower' in spec and 'upper' in spec:
            if spec['lower'] > spec['upper'] or (spec['lower'] == spec['upper'] and
                    not (spec.get('lower_inclusive', True) and spec.get('upper_inclusive', True))):
                raise ResultError('empty bound interval')
    if {k[0] for k in keys} != {s['test'] for s in specs}:
        raise ResultError('every expected test requires a specification')
    if sum(sum(k[0] == s['test'] for k in keys) for s in specs) > MAX_OUTPUTS:
        raise ResultError('report exceeds 4096 measurements; split contract')
    if len(canonical(contract)) > 60000:
        raise ResultError('contract size exceeds 60 KB; split by test')
    return contract


def compare_bounds(value, spec):
    """Shared inclusive/exclusive finite scalar comparison; margin is in spec units."""
    margins = []
    passed = True
    for side in ('lower', 'upper'):
        if side in spec:
            margin = value - spec[side] if side == 'lower' else spec[side] - value
            if not finite(margin): raise ResultError('nonfinite specification margin')
            margins.append(margin)
            passed &= margin >= 0 if spec.get(side + '_inclusive', True) else margin > 0
    return ('pass' if passed else 'fail'), min(margins)


def evaluate(snapshot, contract):
    """Evaluate declared units/bounds; never infer units, limits or coverage."""
    validate_contract(contract)
    if snapshot.get('schema') != SCHEMA: raise ResultError('unsupported result schema')
    tests = {(v['test'], v['corner'], v['point']): v for v in snapshot['tests']}
    outputs = {(v['test'], v['corner'], v['point'], v['output']): v for v in snapshot['outputs']}
    expected = {(v['test'], v['corner'], v['point']) for v in contract['expected_test_points']}
    extra = sorted(set(tests) - expected)
    missing_tests = sorted(expected - set(tests))
    rows, summaries = [], []
    for spec in contract['specifications']:
        selected = []
        for key in sorted(k for k in expected if k[0] == spec['test']):
            output = outputs.get((*key, spec['output']))
            row = dict(test=key[0], corner=key[1], point=key[2], output=spec['output'],
                       unit=spec['unit'], unit_source='explicit_contract', value=None, margin=None)
            if key not in tests:
                row.update(status='missing', reason='test_point_missing')
            elif tests[key]['status'] != 'done':
                row.update(status='missing', reason='test_not_done')
            elif output is None or output['status'] != 'scalar':
                row.update(status='missing', reason=output['status'] if output else 'output_missing')
            else:
                value = output['value']
                status, margin = compare_bounds(value, spec)
                row.update(value=value, margin=margin, status=status, reason=None)
            selected.append(row)
        measured = [r for r in selected if r['margin'] is not None]
        # Margin units are per-spec; no cross-unit ranking.
        worst = min(measured, key=lambda r: (r['margin'], r['status'] != 'fail')) if measured else None
        summaries.append({'test': spec['test'], 'output': spec['output'], 'unit': spec['unit'],
                          'counts': {s: sum(r['status'] == s for r in selected) for s in ('pass', 'fail', 'missing')},
                          'worst': worst})
        rows.extend(selected)
    if len(rows) > MAX_OUTPUTS:
        raise ResultError('report exceeds 4096 measurements; split contract')
    counts = {s: sum(r['status'] == s for r in rows) for s in ('pass', 'fail', 'missing')}
    complete = not extra and not missing_tests and not counts['missing']
    verdict = 'fail' if counts['fail'] else ('pass' if complete else 'incomplete')
    return {'schema': REPORT_SCHEMA, 'result_sha256': digest(snapshot), 'contract_sha256': digest(contract),
            'target': snapshot['target'], 'history': snapshot['history'], 'contract': contract,
            'library_path': snapshot['library_path'], 'coordinates': snapshot['coordinates'],
            'qualification': verdict, 'spec_qualified': {'pass': True, 'fail': False, 'incomplete': None}[verdict],
            'coverage_complete': complete, 'missing_test_points': missing_tests,
            'unexpected_test_points': extra, 'counts': counts, 'summaries': summaries, 'rows': rows,
            'qualification_scope': 'explicit_contract_only', 'unit_policy': 'declared_no_conversion'}

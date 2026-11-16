"""Conditional CDF input rules publish and stay enforceable for design consumers."""

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard import gate
from cadai.pdk_standard.publication import publish
from cadai.pdk_standard.validate import cdf
from cadai.pdk_standard.workspace import select
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from pdk_standard_fixture import replace, update

MODE_WHEN_W = {'op': 'ne', 'left': {'param': 'mode'}, 'right': 'width'}
MODE_WHEN_AREA = {'op': 'ne', 'left': {'param': 'mode'}, 'right': 'area'}
R1 = {'when': {'op': 'eq', 'left': {'param': 'mode'}, 'right': 'area'},
      'check': {'op': 'le', 'left': {'param': 'w'}, 'right': 1.5e-6},
      'message': 'Width exceeds the area-mode limit'}
R2 = {'check': {'op': 'le', 'left': {'param': 'w'}, 'right': {'param': 'fixed'}},
      'message': 'Width must stay within the callback-fixed width'}


def _width():
    return {'type': 'number', 'unit': 'm', 'meaning': 'Test width', 'write': 'allow',
            'requirement': 'explicit', 'default': 0.3,
            'domain': {'kind': 'interval', 'min': 0.1, 'max': 0.5}}


def _cdf(parameters, extra=None):
    return {'source': 'user', 'depends_on': [], 'presence': 'present',
            'parameters': parameters, 'execution': {'mode': 'literal'}, **(extra or {})}


def test_joint_rules_fail_closed_with_three_valued_logic():
    params = {'w': _width(), 'l': _width()}
    rule = {'cdf': _cdf(params, {'rules': {
        'R1': {'check': {'op': 'ge', 'left': {'param': 'w'}, 'right': {'param': 'l'}},
               'message': 'w must cover l'},
        'R2': {'when': {'op': 'eq', 'left': {'param': 'w'}, 'right': 0.1},
               'check': {'op': 'eq', 'left': 1, 'right': 2}, 'message': 'guarded rule'}}}),
        'use': 'circuit'}
    cdf(rule['cdf'])
    assert gate.values(rule, {'w': 0.5, 'l': 0.1})['w'] == 0.5
    assert gate.values(rule, {'w': 0.2, 'l': 0.1})['w'] == 0.2
    with pytest.raises(PdkUnavailable, match='w must cover l'):
        gate.values(rule, {'w': 0.2, 'l': 0.3})
    with pytest.raises(PdkUnavailable, match='guarded rule'):
        gate.values(rule, {'w': 0.1, 'l': 0.1})
    params['l'].update(write='deny', requirement='not_applicable',
                       default={'state': 'not_applicable', 'reason': 'derived'})
    with pytest.raises(PdkUnavailable, match='unresolved'):
        gate.values(rule, {'w': 0.2})


def test_conditional_domain_resolves_by_selected_case():
    mode = {'type': 'string', 'unit': '1', 'meaning': 'Input mode', 'write': 'allow',
            'requirement': 'explicit', 'default': 'long',
            'domain': {'kind': 'set', 'values': ['long', 'short']}}
    w = _width()
    w['default'] = {'state': 'unknown', 'reason': 'mode dependent'}
    w['domain'] = {'kind': 'conditional', 'cases': [
        {'when': {'op': 'eq', 'left': {'param': 'mode'}, 'right': 'long'},
         'domain': {'kind': 'interval', 'min': 1e-6, 'max': 2e-6}},
        {'when': {'op': 'eq', 'left': {'param': 'mode'}, 'right': 'short'},
         'domain': {'kind': 'interval', 'min': 1e-7, 'max': 5e-7}}]}
    rule = {'cdf': _cdf({'mode': mode, 'w': w}), 'use': 'circuit'}
    cdf(rule['cdf'])
    assert gate.values(rule, {'mode': 'long', 'w': 1e-6})['w'] == 1e-6
    assert gate.values(rule, {'mode': 'short', 'w': 5e-7})['w'] == 5e-7
    with pytest.raises(PdkUnavailable, match='constraints'):
        gate.values(rule, {'mode': 'short', 'w': 1e-6})


def _conditional_ops(with_rule=True, with_fixed=False):
    ops = [
        replace('/parameters/mode/type', 'string'),
        replace('/parameters/mode/unit', '1'),
        replace('/parameters/mode/meaning', 'Input mode'),
        replace('/parameters/mode/domain', {'kind': 'set', 'values': ['width', 'area']}),
        replace('/parameters/mode/write', 'allow'),
        replace('/parameters/mode/requirement', 'explicit'),
        replace('/parameters/mode/default', 'width'),
        replace('/parameters/w/write', 'conditional'),
        {'op': 'add', 'path': '/parameters/w/write_when', 'value': MODE_WHEN_W},
        replace('/parameters/w/requirement', 'conditional'),
        {'op': 'add', 'path': '/parameters/w/required_when', 'value': MODE_WHEN_W},
        replace('/parameters/w/default', 1e-6),
        replace('/parameters/area/unit', 'm2'),
        replace('/parameters/area/meaning', 'Area'),
        replace('/parameters/area/domain', {'kind': 'interval', 'min': 1e-15, 'max': 1e-6}),
        replace('/parameters/area/write', 'conditional'),
        {'op': 'add', 'path': '/parameters/area/write_when', 'value': MODE_WHEN_AREA},
        replace('/parameters/area/requirement', 'conditional'),
        {'op': 'add', 'path': '/parameters/area/required_when', 'value': MODE_WHEN_AREA},
        replace('/parameters/area/default', 2e-12),
        replace('/interface_parameters', ['mode', 'w', 'area']),
    ]
    if with_fixed:
        ops.extend([
            replace('/parameters/fixed/meaning', 'Fixed by callback'),
            replace('/parameters/fixed/write', 'deny'),
            replace('/parameters/fixed/requirement', 'not_applicable'),
            replace('/parameters/fixed/default',
                    {'state': 'not_applicable', 'reason': 'Derived by callback'}),
            replace('/parameters/fixed/domain',
                    {'kind': 'unknown', 'reason': 'Derived by callback'}),
        ])
    if with_rule:
        rules = {'R1': R1, **({'R2': R2} if with_fixed else {})}
        ops.append({'op': 'add', 'path': '/rules', 'value': rules})
    return ops


def _conditional_package(workspace, *, with_rule=True, with_fixed=False, execution=None):
    named = ('mode', 'area') + (('fixed',) if with_fixed else ())
    current, _, _ = ready_candidate(workspace, named=named)
    ops = _conditional_ops(with_rule=with_rule, with_fixed=with_fixed)
    ops.append(replace('/execution', execution or
                       {'mode': 'cdf', 'order': ['mode', 'w', 'area']}))
    updates, preview = update(workspace, current, ops, 'cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    return select(workspace, 'synthetic', {})


def test_conditional_callback_constraints_publish_and_consume(tmp_path):
    current = _conditional_package(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {},
            acceptance=acceptance_evidence(tmp_path))
    current = select(tmp_path, 'synthetic', {})
    rule = {'cdf': current.document('cell0/cdf.json'), 'use': 'circuit',
            'revision': current.revision}
    assert gate.values(rule, {'mode': 'width', 'area': 2e-12})['w'] == 1e-6
    assert gate.values(rule, {'mode': 'area', 'w': 1e-6})['area'] == 2e-12
    with pytest.raises(PdkUnavailable, match='denied'):
        gate.values(rule, {'mode': 'width', 'area': 2e-12, 'w': 1e-6})
    with pytest.raises(PdkUnavailable, match='denied'):
        gate.values(rule, {'mode': 'area', 'w': 1e-6, 'area': 2e-12})
    with pytest.raises(PdkUnavailable, match='Explicit'):
        gate.values(rule, {'mode': 'width'})
    with pytest.raises(PdkUnavailable, match='area-mode limit'):
        gate.values(rule, {'mode': 'area', 'w': 2e-6})


def test_callback_operand_must_be_a_writable_input(tmp_path):
    blocked = _conditional_package(tmp_path / 'blocked', with_fixed=True)
    with pytest.raises(PdkUnavailable, match='conditional_operand_not_writable'):
        publish(tmp_path / 'blocked', 'synthetic', blocked.revision, {})
    literal = _conditional_package(tmp_path / 'literal', with_fixed=True,
                                   execution={'mode': 'literal'})
    publish(tmp_path / 'literal', 'synthetic', literal.revision, {},
            acceptance=acceptance_evidence(tmp_path / 'literal'))
    literal = select(tmp_path / 'literal', 'synthetic', {})
    rule = {'cdf': literal.document('cell0/cdf.json'), 'use': 'circuit',
            'revision': literal.revision}
    with pytest.raises(PdkUnavailable, match='unresolved'):
        gate.values(rule, {'mode': 'width', 'area': 2e-12})

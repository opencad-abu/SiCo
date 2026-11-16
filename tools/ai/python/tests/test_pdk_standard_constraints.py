"""Numerical/conditional acceptance and design values use complete server-side rules."""

from copy import deepcopy

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.constraints import accepts, condition
from cadai.pdk_standard.validate import cdf
from cadai.pdk_standard.gate import values
from cadai.pdk_standard.patches import apply


def width():
    return {'type': 'number', 'unit': 'm', 'meaning': 'Test width', 'write': 'allow',
            'requirement': 'explicit', 'default': 0.3, 'domain': {'kind': 'interval', 'min': 0.1, 'max': 0.5},
            'grid': {'step': 0.1, 'origin': 0}, 'step': {'mode': 'ratio', 'value': 2}}


def rules(parameters=None, extra=None):
    return {'source': 'user', 'depends_on': [], 'presence': 'present',
            'parameters': parameters or {'w': width()}, 'execution': {'mode': 'literal'}, **(extra or {})}


@pytest.mark.parametrize('value,accepted', [(0.1, True), (0.3, True), (0.5, True), (0.30001, False),
                                           (0.6, False), (True, False), ('0.3', False)])
def test_decimal_lattice_and_step_is_only_advice(value, accepted):
    assert accepts(value, width()) is accepted


def test_explicit_default_is_a_valid_decision_and_out_of_range_is_rejected():
    rule = {'cdf': rules(), 'use': 'circuit'}
    assert values(rule, {'w': 0.3})['w'] == 0.3
    with pytest.raises(PdkUnavailable, match='Explicit'):
        values(rule, {})
    with pytest.raises(PdkUnavailable, match='constraints'):
        values(rule, {'w': 0.31})
    rule['cdf']['parameters']['w']['write'] = 'deny'
    rule['cdf']['parameters']['w']['requirement'] = 'not_applicable'
    with pytest.raises(PdkUnavailable, match='denied'):
        values(rule, {'w': 0.3})


def test_condition_three_values_and_type_units():
    params = {'w': width(), 'l': width()}
    compare = {'op': 'ge', 'left': {'param': 'w'}, 'right': 0.2}
    assert condition(compare, params, {}) is None
    assert condition({'any': [compare, {'op': 'eq', 'left': 1, 'right': 1}]}, params, {}) is True
    assert condition({'all': [compare, {'op': 'eq', 'left': 1, 'right': 2}]}, params, {}) is False
    with pytest.raises(PdkUnavailable, match='type'):
        condition({**compare, 'right': '0.2'}, params, {'w': 0.3})
    params['l']['unit'] = 'V'
    with pytest.raises(PdkUnavailable, match='units'):
        condition({**compare, 'right': {'param': 'l'}}, params)
    with pytest.raises(PdkUnavailable, match='32 comparisons'):
        condition({'all': [{'all': [compare] * 20}] * 2}, params)


def test_conditional_default_and_ambiguity_are_checked():
    params = {'w': width(), 'l': width()}
    when = {'op': 'ge', 'left': {'param': 'l'}, 'right': 0.2}
    case = {'when': when, 'domain': {'kind': 'interval', 'min': 0.2, 'max': 0.5}}
    params['w']['domain'] = {'kind': 'conditional', 'cases': [case]}
    cdf(rules(params))
    assert accepts(0.3, params['w'], {'definitions': params, 'values': {'l': 0.3}})
    assert accepts(0.3, params['w'], {'definitions': params, 'values': {}}) is None
    params['w']['domain']['cases'].append(deepcopy(case))
    with pytest.raises(PdkUnavailable, match='Overlapping'):
        cdf(rules(params))


def test_impossible_grid_and_cyclic_dependency():
    p = width()
    p.update(default={'state': 'unknown', 'reason': 'not observed'},
             domain={'kind': 'interval', 'min': 0.11, 'max': 0.19})
    with pytest.raises(PdkUnavailable, match='common value'):
        cdf(rules({'w': p}))
    params = {'w': width(), 'l': width()}
    params['w']['depends_on'] = ['l']
    params['l']['depends_on'] = ['w']
    with pytest.raises(PdkUnavailable, match='Cyclic'):
        cdf(rules(params))


def test_json_pointer_and_object_patch_semantics():
    source = {'p/a': {'~w': 1}, 'arr': [1, 2]}
    assert apply(source, [{'op': 'replace', 'path': '/p~1a/~0w', 'value': 2}])['p/a']['~w'] == 2
    for operation in ({'op': 'add', 'path': '/arr', 'value': []},
                      {'op': 'replace', 'path': '/absent', 'value': 1},
                      {'op': 'remove', 'path': '/arr/0'}, {'op': 'remove', 'path': '/bad~2'}):
        with pytest.raises(PdkUnavailable):
            apply(source, [operation])

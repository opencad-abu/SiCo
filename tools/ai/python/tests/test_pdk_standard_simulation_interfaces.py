"""The simInfo projection must reproduce the reviewed interface conventions."""

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.simulation_interfaces import pairs, parameter_mapping, project, unwrap


def known(value):
    if isinstance(value, list):
        return {'type': 'list', 'status': 'known', 'value': [known(v) for v in value]}
    if isinstance(value, str):
        return {'type': 'symbol', 'status': 'known', 'value': value}
    return {'type': 'nil', 'status': 'known', 'value': value}


def simulator(name, order, mapping, props, params, component='subcircuit'):
    return {'name': name, 'raw': {
        'termOrder': known(order),
        'termMapping': known([None] + mapping),
        'propMapping': known([None] + props),
        'instParameters': known(params),
        'componentName': known(component),
    }}


MOS = simulator(
    'hspiceD', ['D', 'G', 'S', 'B'],
    ['D', '(FUNCTION mappedRoot("^D,isub"))', 'G', '(FUNCTION mappedRoot("^G,isub"))',
     'S', '(FUNCTION mappedRoot("^S,isub"))', 'B', '(FUNCTION mappedRoot("^B,isub"))'],
    ['mr', 'simM', 'nf', 'fingers', 'l', 'lgb', 'dps', 'DPS', 'vds', 'Vds'],
    ['w', 'l', 'nf', 'mr', 'dps'])
SPECTRE = simulator(
    'spectre', ['D', 'G', 'S', 'B'],
    ['D', ':1', 'G', ':2', 'S', ':3', 'B', ':4'],
    ['mr', 'simM', 'nf', 'fingers', 'l', 'lgb'],
    ['w', 'l', 'nf', 'mr'])


def test_unwrap_and_pairs_follow_the_typed_envelope():
    assert unwrap(known(['a', 'b'])) == ['a', 'b']
    assert pairs(unwrap(known([None, 'm', 'simM', 'l', 'lgb']))) == [('m', 'simM'), ('l', 'lgb')]
    assert pairs(None) == [] and pairs([]) == []
    with pytest.raises(PdkUnavailable, match='pair list'):
        pairs([None, 'a'])


DECKS = {'hspiceD': {'ports': ['d', 'g', 's', 'b'], 'kind': 'subckt'},
         'spectre': {'ports': ['d', 'g', 's', 'b'], 'kind': 'subckt'}}


def test_projection_matches_the_reviewed_conventions():
    interfaces, missing = project([MOS, SPECTRE], 'n09_ckt',
                                  {'hspiceD': ['hspiceD.tt'], 'spectre': ['spectre.tt']}, DECKS)
    assert missing == []
    hspice = interfaces['hspiceD']
    assert hspice['term_order'] == ['d', 'g', 's', 'b']
    assert hspice['terminal_map'] == {'D': 'd', 'G': 'g', 'S': 's', 'B': 'b'}
    assert hspice['parameters']['l'] == {'cdf': 'lgb'}
    assert hspice['parameters']['nf'] == {'cdf': 'fingers'}
    assert hspice['parameters']['w'] == {'cdf': 'w'}
    assert 'vds' not in hspice['parameters']
    assert interfaces['spectre']['terminal_map'] == {'D': 'd', 'G': 'g', 'S': 's', 'B': 'b'}
    assert interfaces['spectre']['model_refs'] == ['spectre.tt']


def test_ipar_defaults_record_the_registered_tool_synchronized_mapping():
    expected = {'derived': 'tool-synchronized instance parameter (iPar("m"))',
                'implementation_ref': 'builtin:ipar-sync:v1'}
    assert parameter_mapping('simM', 'iPar("m")') == expected
    assert parameter_mapping('w', '1u') == {'cdf': 'w'}
    interfaces, missing = project([MOS, SPECTRE], 'n09_ckt',
                                  {'hspiceD': ['hspiceD.tt'], 'spectre': ['spectre.tt']}, DECKS,
                                  defaults={'simM': 'iPar("m")'})
    assert missing == []
    assert interfaces['hspiceD']['parameters']['mr'] == expected
    assert interfaces['spectre']['parameters']['mr'] == expected
    assert interfaces['hspiceD']['parameters']['l'] == {'cdf': 'lgb'}


def test_projection_reports_missing_evidence_instead_of_guessing():
    interfaces, missing = project([MOS], 'n09_ckt', {}, {'hspiceD': DECKS['hspiceD']})
    assert missing == ['spectre']
    assert set(interfaces) == {'hspiceD'}
    with pytest.raises(PdkUnavailable, match='Deck ports do not match'):
        project([MOS], 'n09_ckt', {}, {'hspiceD': {'ports': ['a', 'b'], 'kind': 'subckt'}})
    with pytest.raises(PdkUnavailable, match='deck kind'):
        project([MOS], 'n09_ckt', {}, {'hspiceD': {'ports': ['a', 'b', 'c', 'd'], 'kind': 'x'}})


DIODE = simulator(
    'hspiceD', ['PLUS', 'MINUS'], ['PLUS', '', 'MINUS', '(FUNCTION minus(root("PLUS")))'],
    ['m', 'simM'], ['area', 'pj', 'm'],
    component='diode')


def test_model_kind_elements_use_the_confirmed_letter_roles():
    interfaces, missing = project([DIODE], 'ndio09', {'hspiceD': ['hspiceD.dio_tt']},
                                  {'hspiceD': {'kind': 'model'}})
    assert missing == ['spectre']
    row = interfaces['hspiceD']
    assert row['kind'] == 'model'
    assert row['term_order'] == ['a', 'b']
    assert row['terminal_map'] == {'PLUS': 'a', 'MINUS': 'b'}
    assert row['parameters']['m'] == {'cdf': 'simM'}


THREE_T = simulator(
    'hspiceD', ['D', 'G', 'S', 'B'],
    ['D', '(FUNCTION mappedRoot("^D,isub"))', 'G', '(FUNCTION mappedRoot("^G,isub"))',
     'S', '(FUNCTION mappedRoot("^S,isub"))', 'B', '(FUNCTION mappedRoot("^S,isub"))'],
    [], ['w', 'l'])
SPECTRE_THREE_T = simulator(
    'spectre', ['D', 'G', 'S', 'B'], ['D', ':1', 'G', ':2', 'S', ':3', 'B', ':4'], [], ['w', 'l'])
FOUR_PORT = {'ports': ['d', 'g', 's', 'b'], 'kind': 'subckt'}


def test_missing_symbol_terminals_become_proven_implicit_terminals():
    interfaces, missing = project([THREE_T, SPECTRE_THREE_T], 'n09_ckt', {},
                                  {'hspiceD': FOUR_PORT, 'spectre': FOUR_PORT},
                                  terminals={'D', 'G', 'S'})
    assert missing == []
    for name in ('hspiceD', 'spectre'):
        row = interfaces[name]
        assert row['terminal_map'] == {'D': 'd', 'G': 'g', 'S': 's'}
        assert row['implicit_terminals'] == {'b': 's'}
    with pytest.raises(PdkUnavailable, match='no confirmed connection'):
        project([SPECTRE_THREE_T], 'n09_ckt', {}, {'spectre': FOUR_PORT}, terminals={'D', 'G', 'S'})


GLOBAL_THREE_T = simulator(
    'hspiceD', ['D', 'G', 'S', 'B'],
    ['D', '(FUNCTION mappedRoot("^D,isub"))', 'G', '(FUNCTION mappedRoot("^G,isub"))',
     'S', '(FUNCTION mappedRoot("^S,isub"))', 'B', '(FUNCTION mappedRoot("^b,isub"))'],
    [], ['w', 'l'])


def test_an_explicit_global_net_is_a_valid_implicit_terminal_basis():
    interfaces, missing = project([GLOBAL_THREE_T, SPECTRE_THREE_T], 'n09_ckt', {},
                                  {'hspiceD': FOUR_PORT, 'spectre': FOUR_PORT},
                                  terminals={'D', 'G', 'S'})
    assert missing == []
    assert interfaces['hspiceD']['implicit_terminals'] == {'b': 'b'}


def test_four_terminal_mos_model_cards_use_the_registered_role_order():
    interfaces, missing = project([MOS, SPECTRE], 'n18', {},
                                  {'hspiceD': {'kind': 'model', 'model_type': 'nmos'},
                                   'spectre': {'kind': 'model', 'model_type': 'bsim4'}},
                                  terminals={'D', 'G', 'S', 'B'})
    assert missing == []
    assert interfaces['hspiceD']['term_order'] == ['d', 'g', 's', 'b']
    assert interfaces['spectre']['term_order'] == ['d', 'g', 's', 'b']
    with pytest.raises(PdkUnavailable, match='No registered terminal convention'):
        project([MOS, SPECTRE], 'n18', {},
                {'hspiceD': {'kind': 'model', 'model_type': 'mystery'},
                 'spectre': {'kind': 'model', 'model_type': 'bsim4'}},
                terminals={'D', 'G', 'S', 'B'})

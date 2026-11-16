"""Deck reads must follow real section and .inc structure without guessing."""

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.model_decks import (
    declares,
    declarations,
    expand,
    includes,
    model_card,
    resolve,
    sections,
    subckt_ports,
)

HS = """.lib tt
.subckt n09_ckt d g s b w=1e-6 l=1e-6
+dvt_n09 = 0
.endl tt
.lib res_tt
.inc 'res.ckt'
.endl res_tt
.lib dio_tt
.inc 'dio.mdl'
+drs_ndio09 = 0
.endl dio_tt
.lib bjt_tt
.subckt npn09a4_ckt c b e psub mismod_bjt = 1
.endl bjt_tt
"""

SPE = """section tt
inline subckt n09_ckt ( d g s b )
endsection tt
section res_tt
include "res_spe.ckt"
endsection res_tt
"""

FILES = {
    'lib': HS,
    'res.ckt': '.subckt ralpa_2t_ckt in out l=lr w=wr mr=1\nd1 sx in ndio09 area=1\n',
    'dio.mdl': '.model ndio09 d level = 3\n',
    'spe.lib': SPE,
    'res_spe.ckt': 'subckt ralpa_2t_ckt (in out)\n',
    'loop.ckt': ".inc 'loop.ckt'\nleaf\n",
}


def load(path):
    return FILES[path]


def test_sections_and_includes_read_both_syntaxes():
    assert sorted(sections(HS)) == ['bjt_tt', 'dio_tt', 'res_tt', 'tt']
    assert sorted(sections(SPE)) == ['res_tt', 'tt']
    assert includes(HS)[:2] == ['res.ckt', 'dio.mdl']
    assert includes(SPE) == ['res_spe.ckt']


def test_expand_follows_includes_with_bounded_depth():
    body = sections(HS)['res_tt']
    closure = expand(body, load)
    assert 'ralpa_2t_ckt' in closure
    looped = expand(sections(HS)['res_tt'], lambda path: load('loop.ckt') if path == 'loop.ckt' else load(path))
    assert 'ralpa_2t_ckt' in looped and looped.count('leaf') <= 4


def test_token_and_declaration_rules_use_underscores_as_separators():
    assert declares('.lib dio_tt\n+drsh_ralpa_2t_ckt = 0\n.model ndio09 d', 'ndio09')
    assert not declares('+drs_ndio09uhvt = 0', 'ndio09')
    assert declares('.subckt n09_ckt d g s b', 'n09_ckt')
    assert not declares('* table mentions ndio09\n// also ndio09', 'ndio09')
    assert declarations('inline subckt x ( a b )\n.model y d') == {'x': 'subckt', 'y': 'model'}


def test_element_references_inside_a_macro_model_do_not_declare():
    closure = expand(sections(HS)['res_tt'], load)
    assert declares(closure, 'ralpa_2t_ckt')
    assert not declares(closure, 'ndio09')


def test_ports_and_model_cards_read_the_headers():
    assert subckt_ports(HS, 'n09_ckt') == ['d', 'g', 's', 'b']
    assert subckt_ports(HS, 'npn09a4_ckt') == ['c', 'b', 'e', 'psub']
    assert subckt_ports(SPE, 'n09_ckt') == ['d', 'g', 's', 'b']
    assert subckt_ports(HS, 'missing') is None
    assert model_card(FILES['dio.mdl'], 'ndio09') == 'd'
    assert model_card('.model npn09 d\n', 'ndio09') is None


def entries(lib, section, config, path='lib'):
    return [(config, section, path, load)]


def test_resolve_returns_refs_ports_and_kind():
    refs, ports, kind = resolve('ralpa_2t_ckt', 'hspiceD',
                                entries(HS, 'res_tt', 'hspiceD.res_tt'))
    assert refs == ['hspiceD.res_tt'] and ports == ['in', 'out'] and kind == 'subckt'
    refs, ports, kind = resolve('ndio09', 'hspiceD', entries(HS, 'dio_tt', 'hspiceD.dio_tt'))
    assert refs == ['hspiceD.dio_tt'] and ports is None and kind == 'model'
    refs, ports, kind = resolve('ndio09', 'hspiceD', entries(HS, 'res_tt', 'hspiceD.res_tt'))
    assert refs == [] and ports is None and kind is None
    refs, ports, kind = resolve('n09_ckt', 'spectre', entries(SPE, 'tt', 'spectre.tt', 'spe.lib'))
    assert refs == ['spectre.tt'] and ports == ['d', 'g', 's', 'b'] and kind == 'subckt'
    refs, ports, kind = resolve('ralpa_2t_ckt', 'spectre', entries(SPE, 'res_tt', 'spectre.res_tt', 'spe.lib'))
    assert refs == ['spectre.res_tt'] and ports == ['in', 'out'] and kind == 'subckt'
    refs, ports, kind = resolve('p09_ckt', 'hspiceD', entries(HS, 'tt', 'hspiceD.tt'))
    assert refs == [] and ports is None and kind is None
    with pytest.raises(PdkUnavailable, match='section missing'):
        resolve('n09_ckt', 'hspiceD', entries(HS, 'nope', 'hspiceD.nope'))


def test_collection_reuses_an_already_confirmed_setup():
    from cadai.pdk_standard.simulation_collection import confirmed_setup

    setup = {'switch_views': ['spectre', 'schematic'], 'stop_views': ['spectre'],
             'options': {'scale': 0.9}}

    class Package:
        def document(self, name):
            if name == 'device.json':
                return {'items': {'a': {'dir': 'a'},
                                  'b': {'dir': 'b'}}}
            return {'setup': setup}

        def available(self, name):
            return name == 'a/simulation.json'

    assert confirmed_setup(Package()) == setup

    class Empty(Package):
        def available(self, name):
            return False

    assert confirmed_setup(Empty()) is None

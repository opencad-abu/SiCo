"""The builtin projection is evidence-bound and fail-closed."""

import json

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.readiness import netlist_invisible_basis
from cadai.pdk_standard.simulation_builtin import (
    PROBE_SCHEMA,
    check_line,
    load,
    netlist_name,
    project,
    transmitted,
)


def known(value):
    if isinstance(value, list):
        return {'type': 'list', 'status': 'known', 'value': [known(v) for v in value]}
    if isinstance(value, str):
        return {'type': 'symbol', 'status': 'known', 'value': value}
    return {'type': 'nil', 'status': 'known', 'value': value}


def sim(name, component, prefix, inst, props=None):
    raw = {
        'termOrder': known(['PLUS', 'MINUS']),
        'termMapping': known([None, 'PLUS', ':1', 'MINUS', '(FUNCTION minus(root("PLUS")))']),
        'instParameters': known(inst),
        'componentName': known(component),
        'namePrefix': known(prefix),
    }
    if props is not None:
        raw['propMapping'] = known([None, *props])
    return {'name': name, 'raw': raw}


SPECTRE = sim('spectre', 'resistor', '', ['r', 'type', 'blank', 'num'], ['type', 'srcType'])
HSPICE = sim('hspiceD', 'res', 'R', ['r', 'blank'])

CDF = {
    'interface_parameters': ['r'],
    'parameters': {
        'r': {'write': 'allow', 'requirement': 'default', 'unit': 'Ohm', 'default': 1000},
        'srcType': {'write': 'unknown', 'requirement': 'unknown', 'unit': '1', 'default': 'pulse'},
        'blank': {'write': 'unknown', 'requirement': 'unknown', 'unit': '1', 'default': ' '},
        'num': {'write': 'deny', 'requirement': 'not_applicable', 'unit': 'm', 'default': 0},
    },
}

OBSERVED = {
    'spectre': {'netlist': 'R1 (vdd 0) resistor r=1000 type=pulse'},
    'hspiceD': {'netlist': 'r1 vdd 0 1e3'},
}

PROBE = {
    'schema_version': PROBE_SCHEMA,
    'runs': {'spectre': {'version': '23.10.538', 'run_ref': '/runs/probe'},
             'hspiceD': {'version': 'IC23.10.130', 'run_ref': '/runs/probe'}},
    'setup': {'switch_views': ['spectre', 'schematic'], 'stop_views': ['spectre'], 'options': {}},
    'devices': {'res': OBSERVED},
}


def test_load_accepts_the_recorded_probe_and_derives_stable_source_ids(tmp_path):
    path = tmp_path / 'probe.json'
    path.write_text(json.dumps(PROBE))
    probe = load(str(path))
    assert probe['path'] == str(path)
    assert probe['sources']['spectre'].startswith('builtin_spectre_')
    assert probe['sources']['hspiceD'] != probe['sources']['spectre']
    assert load(str(path))['sources'] == probe['sources']


@pytest.mark.parametrize('change', [
    {'schema_version': 'sico.pdk.builtin-probe.v2'},
    {'runs': {'spectre': {'version': '23.10.538', 'run_ref': '/runs/probe'}}},
    {'setup': {'switch_views': [], 'stop_views': ['spectre'], 'options': {}}},
    {'devices': {'res': {'spectre': {'netlist': ''}, 'hspiceD': {'netlist': 'r1 1 2'}}}},
    {'devices': {'res': {'veriloga': {'netlist': 'x1 1 2'}}}},
])
def test_load_rejects_incomplete_or_unknown_probe_shapes(tmp_path, change):
    path = tmp_path / 'probe.json'
    path.write_text(json.dumps({**PROBE, **change}))
    with pytest.raises(PdkUnavailable):
        load(str(path))


def test_netlist_name_follows_the_dialect():
    assert netlist_name('spectre', SPECTRE['raw']) == 'resistor'
    assert netlist_name('hspiceD', HSPICE['raw']) == 'R'
    assert netlist_name('spectre', {}) is None


def test_transmitted_keeps_confirmed_inputs_and_fixed_string_tokens():
    parameters, fixed = transmitted('spectre', SPECTRE['raw'], CDF)
    assert parameters == {'r': {'cdf': 'r'}, 'type': {'cdf': 'srcType'}}
    assert fixed == {'type': 'srcType'}


def test_transmitted_refuses_to_drop_a_confirmed_input():
    cdf = {'interface_parameters': ['r', 'missing'], 'parameters': CDF['parameters']}
    with pytest.raises(PdkUnavailable, match='misses confirmed inputs'):
        transmitted('spectre', SPECTRE['raw'], cdf)


def test_project_builds_builtin_interfaces_with_the_confirmed_port_roles():
    interfaces, missing = project([SPECTRE, HSPICE], CDF, OBSERVED, {'PLUS', 'MINUS'})
    assert missing == []
    spectre = interfaces['spectre']
    assert spectre['kind'] == 'primitive' and spectre['model_source'] == 'builtin'
    assert spectre['name'] == 'resistor'
    assert spectre['term_order'] == ['a', 'b']
    assert spectre['terminal_map'] == {'PLUS': 'a', 'MINUS': 'b'}
    assert spectre['parameters'] == {'r': {'cdf': 'r'}, 'type': {'cdf': 'srcType'}}
    assert interfaces['hspiceD']['name'] == 'R'
    assert interfaces['hspiceD']['parameters'] == {'r': {'cdf': 'r'}}


def test_project_reports_a_missing_recorded_line_instead_of_guessing():
    interfaces, missing = project([SPECTRE, HSPICE], CDF, {'hspiceD': OBSERVED['hspiceD']},
                                  {'PLUS', 'MINUS'})
    assert 'spectre:netlist' in missing and 'spectre' not in interfaces


def test_project_rejects_a_recorded_line_that_contradicts_the_projection():
    observed = {'spectre': {'netlist': 'R1 (vdd 0) capacitor r=1000'},
                'hspiceD': OBSERVED['hspiceD']}
    with pytest.raises(PdkUnavailable, match='emitted primitive'):
        project([SPECTRE, HSPICE], CDF, observed, {'PLUS', 'MINUS'})
    with pytest.raises(PdkUnavailable, match='emitted element'):
        project([SPECTRE, HSPICE], CDF,
                {'spectre': OBSERVED['spectre'], 'hspiceD': {'netlist': 'c1 vdd 0 1e3'}},
                {'PLUS', 'MINUS'})


def test_check_line_keeps_terminal_evidence():
    with pytest.raises(PdkUnavailable, match='lacks terminals'):
        check_line('spectre', 'resistor', ['a', 'b'], 'R1 (vdd) resistor r=1')
    with pytest.raises(PdkUnavailable, match='lacks terminals'):
        check_line('hspiceD', 'R', ['a', 'b'], 'r1 vdd')
    with pytest.raises(PdkUnavailable, match='Fixed builtin token'):
        project([SPECTRE, HSPICE], CDF, {'spectre': {'netlist': 'R1 (vdd 0) resistor r=1000'},
                                         'hspiceD': OBSERVED['hspiceD']}, {'PLUS', 'MINUS'})


class FakePackage:
    def __init__(self, sources):
        self.sources = sources

    def document(self, name):
        assert name == 'sources.json'
        return {'items': self.sources}


def test_netlist_invisible_basis_requires_recorded_simulation_runs():
    simulation = {'evidence': {'/interfaces': 'run1', '/interfaces/hspiceD': 'run2'}}
    runs = {'run1': {'kind': 'simulation'}, 'run2': {'kind': 'simulation'}}
    assert netlist_invisible_basis(FakePackage(runs), simulation)
    assert not netlist_invisible_basis(
        FakePackage({**runs, 'run2': {'kind': 'derived'}}), simulation)
    assert not netlist_invisible_basis(FakePackage(runs), {'evidence': {}})
    assert not netlist_invisible_basis(FakePackage(runs), {'evidence': {'/other': 'run1'}})


def test_source_check_verifies_builtin_interface_dependencies():
    """A builtin interface dependency recomputes from the captured simInfo items."""
    from cadai.pdk_standard import source_check
    from cadai.pdk_standard.jsonio import fingerprint

    items = [sim('spectre', 'resistor', '', ['r'])]
    target = {'library': 'analogLib', 'cell': 'res', 'view': 'symbol'}
    ref = 'res_interface'

    class _Package:
        manifest = {'dependencies': {ref: {'kind': 'interface', 'target': target,
                                           'method': 'sico-siminfo-builtin-v1',
                                           'fingerprint': fingerprint(items)}},
                    'libraries': {'analogLib': {'name': 'analogLib', 'root': 'pdk', 'path': '.'}},
                    'items': None}
        roots = {'pdk': '/synthetic'}

        def document(self, name):
            assert name == 'device.json'
            return {'items': {'res': {'library': 'analogLib', 'cell': 'res', 'view': 'symbol'}}}

    value = {'target': target, 'library': {'resolved_path': '/synthetic/res'},
             'simulators': {'status': 'complete', 'items': items}}
    outcome = source_check.outcome(_Package(), 'res', value, ref)
    assert outcome['status'] == 'ok' and outcome['fingerprint'] == fingerprint(items)
    changed = {**value, 'simulators': {'status': 'complete', 'items': items + items}}
    assert source_check.outcome(_Package(), 'res', changed, ref)['status'] == 'changed'
    partial = {**value, 'simulators': {'status': 'partial', 'items': items}}
    assert source_check.outcome(_Package(), 'res', partial, ref)['status'] == 'unverifiable'

"""The same collection and consumption flow covers naming, category and provenance boundaries."""

import copy

import pytest
from pdk_publication_fixture import two_device_candidate as two_devices
from pdk_standard_fixture import replace, update

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard import gate, interface, readiness
from cadai.pdk_standard.constraints import unknown
from cadai.pdk_standard.simulation_builtin import project
from cadai.pdk_standard.simulation_collection import build_builtin
from cadai.pdk_standard.workspace import select


def known(value):
    if isinstance(value, list):
        return {'type': 'list', 'status': 'known', 'value': [known(v) for v in value]}
    if isinstance(value, str):
        return {'type': 'symbol', 'status': 'known', 'value': value}
    return {'type': 'nil', 'status': 'known', 'value': value}


def confirm(tmp_path, package, file, ops):
    updates, preview = update(tmp_path, package, ops, file=file)
    updates.apply({'update_ref': preview['update_ref']})
    return select(tmp_path, 'synthetic', {})


def reasons(package, key):
    row = package.document('device.json')['items'][key]
    issues = readiness.device_issues(package, key, row, 'circuit')
    return sorted({issue['reason'] for issue in issues})


def simulator(name, component, prefix, inst, props=None):
    raw_row = {
        'termOrder': known(['PLUS', 'MINUS']),
        'termMapping': known([None, 'PLUS', ':p', 'MINUS', '(FUNCTION minus(root("PLUS")))']),
        'instParameters': known(inst),
        'componentName': known(component),
        'namePrefix': known(prefix),
    }
    if props is not None:
        raw_row['propMapping'] = known([None, *props])
    return {'name': name, 'raw': raw_row}


def test_different_netlist_names_share_the_same_cdf_input():
    """Different-name-same-meaning: netlist names follow each simulator, not the CDF."""
    spectre = simulator('spectre', 'vsource', '', ['dc', 'val0', 'type'],
                        ['dc', 'vdc', 'val0', 'v1', 'type', 'srcTypePULSE'])
    hspice = simulator('hspiceD', 'vsrc', 'V', ['vdc', 'v1'])
    cdf = {'interface_parameters': ['vdc', 'v1'], 'parameters': {
        'vdc': {'write': 'allow', 'requirement': 'explicit', 'unit': 'V',
                'default': {'state': 'unknown', 'reason': 'caller decided'}},
        'v1': {'write': 'allow', 'requirement': 'default', 'unit': 'V', 'default': 0.0},
        'srcTypePULSE': {'write': 'unknown', 'requirement': 'unknown', 'unit': '1',
                         'default': 'pulse'}}}
    observed = {'spectre': {'netlist': 'V1 (vdd 0) vsource dc=1.2 type=pulse val0=0'},
                'hspiceD': {'netlist': 'v1 vdd 0 DC=1.2'}}
    interfaces, missing = project([spectre, hspice], cdf, observed, {'PLUS', 'MINUS'})
    assert missing == []
    assert interfaces['spectre']['parameters'] == {
        'dc': {'cdf': 'vdc'}, 'val0': {'cdf': 'v1'}, 'type': {'cdf': 'srcTypePULSE'}}
    assert interfaces['hspiceD']['parameters'] == {'vdc': {'cdf': 'vdc'}, 'v1': {'cdf': 'v1'}}
    assert interfaces['spectre']['terminal_map'] == interfaces['hspiceD']['terminal_map'] == {
        'PLUS': 'a', 'MINUS': 'b'}


def test_same_cdf_name_stays_with_its_own_device(tmp_path):
    """Same-name-different-meaning: one shared name carries per-device rules."""
    package = two_devices(tmp_path, second='w', second_prompt='Mode selector word')
    package = confirm(tmp_path, package, 'cell0/cdf.json', [
        replace('/parameters/w/meaning', 'Width in meters'),
        replace('/parameters/w/write', 'allow'),
        replace('/parameters/w/requirement', 'explicit'),
        replace('/parameters/w/domain', {'kind': 'interval', 'min': 1e-7, 'max': 1.0}),
    ])
    package = confirm(tmp_path, package, 'cell1/cdf.json', [
        replace('/parameters/w/type', 'string'),
        replace('/parameters/w/unit', '1'),
        replace('/parameters/w/meaning', 'Mode selector word'),
        replace('/parameters/w/default', 'long'),
        replace('/parameters/w/domain', {'kind': 'set', 'values': ['long', 'short']}),
        replace('/parameters/w/write', 'allow'),
        replace('/parameters/w/requirement', 'explicit'),
    ])
    number = package.document('cell0/cdf.json')
    word = package.document('cell1/cdf.json')
    assert number['parameters']['w']['type'] == 'number'
    assert word['parameters']['w']['type'] == 'string'
    rule_number = {'cdf': number, 'use': 'circuit'}
    rule_word = {'cdf': word, 'use': 'circuit'}
    assert gate.values(rule_number, {'w': 1e-6})['w'] == 1e-6
    assert gate.values(rule_word, {'w': 'short'})['w'] == 'short'
    with pytest.raises(PdkUnavailable):
        gate.values(rule_number, {'w': 'short'})
    with pytest.raises(PdkUnavailable):
        gate.values(rule_word, {'w': 1e-6})


def test_category_labels_do_not_carry_parameter_rules(tmp_path):
    """Category-exception: categories group devices, they never define their rules."""
    package = two_devices(tmp_path, second='l', classify_second=False)
    rows = package.document('device.json')['items']
    assert rows['cell0']['categories'] == ['test']
    assert unknown(rows['cell1']['categories'])
    assert 'category_not_defined' in reasons(package, 'cell1')
    package = confirm(tmp_path, package, 'device.json', [
        replace('/items/cell1/categories', ['test'])])
    rows = package.document('device.json')['items']
    assert rows['cell0']['categories'] == rows['cell1']['categories'] == ['test']
    assert 'category_not_defined' not in reasons(package, 'cell1')
    # both members of the same category keep their own parameter inventories
    assert set(package.document('cell0/cdf.json')['parameters']) == {'w'}
    assert set(package.document('cell1/cdf.json')['parameters']) == {'l'}


def test_user_confirmation_carries_a_device_without_a_manual(tmp_path):
    """No-manual: confirmed user decisions stand without any document source."""
    package = two_devices(tmp_path)
    package = confirm(tmp_path, package, 'cell0/cdf.json', [
        replace('/parameters/w/write', 'allow'),
        replace('/parameters/w/requirement', 'explicit'),
        replace('/parameters/w/meaning', 'Width in meters'),
        replace('/parameters/w/domain', {'kind': 'interval', 'min': 1e-7, 'max': 1.0}),
        replace('/execution', {'mode': 'literal'}),
    ])
    sources = package.document('sources.json')['items']
    kinds = {row['kind'] for row in sources.values()}
    assert 'user' in kinds and 'document' not in kinds
    confirmed = [row for row in sources.values() if row['kind'] == 'user']
    assert confirmed and confirmed[0]['confirmed_by'] == 'test user'
    assert package.document('cell0/cdf.json')['parameters']['w']['write'] == 'allow'
    assert not [issue for issue in readiness.device_issues(
        package, 'cell0', package.document('device.json')['items']['cell0'], 'circuit')
        if '/parameters/w' in issue['path']]


def test_conditional_scope_closes_over_declared_dependencies():
    """Conditional-dependency: declared inputs pull their closure, and a gap fails closed."""
    width = {'type': 'number', 'unit': 'm', 'meaning': 'Width', 'write': 'allow',
             'requirement': 'explicit', 'default': 0.3,
             'domain': {'kind': 'interval', 'min': 1e-7, 'max': 1.0},
             'depends_on': ['mode']}
    mode = {'type': 'string', 'unit': '1', 'meaning': 'Mode', 'write': 'allow',
            'requirement': 'explicit', 'default': 'long',
            'domain': {'kind': 'set', 'values': ['long', 'short']}}
    fixed = {'type': 'number', 'unit': 'm', 'meaning': 'Fixed width', 'write': 'deny',
             'requirement': 'not_applicable', 'default': 1e-7,
             'domain': {'kind': 'interval', 'min': 1e-7, 'max': 1.0}}
    cdf = {'presence': 'present',
           'parameters': {'w': width, 'mode': mode, 'fixed': fixed},
           'execution': {'mode': 'literal'}, 'interface_parameters': ['w'],
           'rules': {'R': {
               'check': {'op': 'le', 'left': {'param': 'w'}, 'right': {'param': 'fixed'}},
               'message': 'w must stay within the fixed width'}}}
    assert interface.parameters(cdf) == {'w', 'mode', 'fixed'}
    broken = copy.deepcopy(cdf)
    del broken['parameters']['mode']
    with pytest.raises(PdkUnavailable, match='dependency'):
        interface.parameters(broken)


def test_missing_support_evidence_is_reported_instead_of_guessed(tmp_path):
    """Missing-field: absent evidence becomes a reported gap, never an inferred fact."""
    package = two_devices(tmp_path)
    row = copy.deepcopy(package.document('device.json')['items']['cell1'])
    del row['dir']
    device_missing = [issue for issue in readiness.device_issues(package, 'cell1', row, 'circuit')
                      if issue['path'] == '/items/cell1/dir']
    assert device_missing and device_missing[0]['reason'] == 'missing_required'
    bare = {'interface_parameters': [], 'parameters': {}}
    spectre = simulator('spectre', 'resistor', '', ['r'])
    hspice = simulator('hspiceD', 'res', 'R', ['r'])
    interfaces, missing = project([spectre, hspice], bare, {}, {'PLUS', 'MINUS'})
    assert set(missing) == {'spectre:netlist', 'hspiceD:netlist'} and interfaces == {}
    hspice['raw']['namePrefix'] = known(None)
    interfaces, missing = project([spectre, hspice], bare,
                                  {'spectre': {'netlist': 'R1 (vdd 0) resistor'},
                                   'hspiceD': {'netlist': 'r1 vdd 0'}},
                                  {'PLUS', 'MINUS'})
    assert 'hspiceD:name' in missing and 'spectre' in interfaces


class _NoPackage:
    def document(self, name):
        raise AssertionError('missing probe evidence must not touch the package: ' + name)


def test_missing_builtin_probe_row_defers_without_reading_the_package():
    document, dependency, note = build_builtin(
        _NoPackage(), {'dir': 'res', 'cell': 'res', 'library': 'synthetic'}, {},
        'source', 'derived', {'devices': {}})
    assert document is None and dependency is None
    assert note == 'builtin probe evidence missing: res'

"""Detailed collection follows confirmed inputs without losing necessary dependencies."""

from copy import deepcopy
from types import SimpleNamespace

from cadai.pdk_standard.coverage import gaps
from pdk_standard_fixture import package


def cdf_gaps(current, cdf):
    proxy = SimpleNamespace(manifest=current.manifest, available=current.available,
        document=lambda name: cdf if name == 'cell0/cdf.json' else current.document(name))
    return {r['path'] for r in gaps(proxy, device='cell0') if r['file'] == 'cell0/cdf.json'}


def test_unknown_scope_is_one_gap_not_all_parameter_details(tmp_path):
    current, _, _, _ = package(tmp_path, 160)
    cdf = current.document('cell0/cdf.json')
    assert cdf_gaps(current, cdf) == {'/parameters'}
    for p in cdf['parameters'].values():
        p.update(write='deny', requirement='not_applicable')
    assert cdf_gaps(current, cdf) == set()


def test_conditional_input_and_recursive_read_dependencies(tmp_path):
    current, _, _, _ = package(tmp_path, 4)
    cdf = current.document('cell0/cdf.json')
    for p in cdf['parameters'].values():
        p.update(write='deny', requirement='not_applicable')
        for field in ('unit', 'meaning', 'default'):
            p[field] = {'state': 'unknown', 'reason': 'Uncollected'}
    condition = {'op': 'eq', 'left': {'param': 'p1'}, 'right': 1}
    cdf['parameters']['w'].update(write='conditional', requirement='conditional',
        write_when=condition, required_when=deepcopy(condition))
    cdf['parameters']['p1']['depends_on'] = ['p2']
    cdf['parameters']['p2']['requirement'] = 'default'
    found = cdf_gaps(current, cdf)
    assert {f'/parameters/w/{f}' for f in ('unit', 'meaning', 'default', 'domain')} <= found
    assert '/parameters/p1/unit' in found and '/parameters/p2/unit' in found
    assert '/parameters/p2/default' in found
    assert not any(p.startswith('/parameters/p3/') for p in found)
    assert not any(p in found for p in ('/parameters/p1/domain', '/parameters/p1/meaning',
                                        '/parameters/p1/default', '/parameters/p2/domain'))


def test_declared_rules_cannot_hide_required_dependency(tmp_path):
    current, _, _, _ = package(tmp_path, 3)
    cdf = current.document('cell0/cdf.json')
    cdf['parameters']['w'].update(write='allow', requirement='explicit')
    cdf['parameters']['p1']['unit'] = {'state': 'unknown', 'reason': 'Not collected'}
    cdf['rules'] = {'limit': {'check': {'op': 'le', 'left': {'param': 'w'},
                                      'right': {'param': 'p1'}}, 'message': 'Limit'}}
    found = cdf_gaps(current, cdf)
    assert '/parameters/p1/unit' in found
    assert '/parameters/p1/domain' not in found
    assert not any(p.startswith('/parameters/p2/') for p in found)

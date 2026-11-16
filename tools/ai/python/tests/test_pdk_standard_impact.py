"""Source-change impact analysis over the declared dependency graph."""

import hashlib
from copy import deepcopy

from sicostate import project_directory

from cadai.pdk_standard.impact import review
from cadai.pdk_standard.ingest import publish
from cadai.pdk_standard.workspace import select
from pdk_standard_fixture import package, replace, update


def with_model_dependency(workspace):
    """A fixture package whose CDF depends on one on-disk model resource."""
    current, bridge, value, _ = package(workspace)
    content = b'model test\n'
    model = workspace / 'models' / 'test.scs'
    model.parent.mkdir(parents=True)
    model.write_bytes(content)
    cdf = current.document('cell0/cdf.json')
    cdf['depends_on'].append('bytes_test')
    resources = current.document('file.json')
    resources['items']['file_test'] = {'kind': 'model', 'root': 'pdk', 'path': 'models/test.scs',
                                       'purpose': 'Test model', 'dependency': 'bytes_test'}
    publish(project_directory(workspace, 'ai/pdk-data', create=True), current.base,
            {'cell0/cdf.json': cdf, 'file.json': resources},
            dependencies={'bytes_test': {'kind': 'model', 'target': 'file_test',
                                         'fingerprint': 'sha256:' + hashlib.sha256(content).hexdigest(),
                                         'method': 'sha256-bytes-v1'}})
    return select(workspace, 'synthetic', {}), bridge, model


def test_review_maps_dependencies_and_counts_the_unaffected(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    report = review(tmp_path, {}, None, {'library': 'synthetic'})
    assert report['revision'] == current.revision and report['status'] == 'ok'
    assert report['byte_dependencies']['checked'] == 0
    assert report['live_required']['dependencies'] == 3
    assert report['live_required']['kinds'] == {'cdf': 1, 'interface': 1, 'symbol': 1}
    assert report['live_required']['devices'] == 1
    assert report['impact']['changed'] == [] and not report['impact']['truncated']
    assert report['impact']['unaffected']['devices'] == 1
    assert report['impact']['unaffected']['documents'] >= 5
    assert report['device_check'] is None


def test_changed_byte_dependency_reports_its_scope(tmp_path):
    current, bridge, model = with_model_dependency(tmp_path)
    report = review(tmp_path, {}, None, {'library': 'synthetic'})
    assert report['status'] == 'ok'
    assert report['byte_dependencies'] == {'checked': 1, 'ok': 1, 'changed': [], 'missing': [],
                                           'unbound': []}
    model.write_bytes(b'model drifted\n')
    report = review(tmp_path, {}, None, {'library': 'synthetic'})
    assert report['status'] == 'incomplete'
    assert report['byte_dependencies']['changed'] == ['bytes_test']
    entry = report['impact']['changed'][0]
    assert entry['status'] == 'changed' and entry['kind'] == 'model'
    assert entry['resource'] == 'models/test.scs'
    assert entry['affected_devices'] == ['cell0'] and entry['affected_documents'] >= 1
    assert report['impact']['unaffected']['devices'] == 0


def test_changed_dependency_lists_affected_confirmations_and_keeps_the_rest(tmp_path):
    current, bridge, model = with_model_dependency(tmp_path)
    updates, preview = update(tmp_path, current, [replace('/parameters/w/meaning', 'Width')],
                              file='cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    updates, preview = update(tmp_path, current, [replace('/bbox', [[-1, -1], [1, 1]])],
                              file='cell0/symbol.json')
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    sources = current.document('sources.json')['items']
    cdf = current.document('cell0/cdf.json')
    affected = sorted(ref for ref in {cdf['source'], *cdf['evidence'].values()}
                      if sources.get(ref, {}).get('kind') == 'user')
    users = sum(1 for row in sources.values() if row.get('kind') == 'user')
    assert len(affected) == 1 and users == 2
    model.write_bytes(b'changed again\n')
    report = review(tmp_path, {}, None, {'library': 'synthetic'})
    entry = report['impact']['changed'][0]
    assert entry['ref'] == 'bytes_test'
    assert entry['affected_confirmations'] == affected
    assert report['impact']['unaffected']['confirmations'] == users - 1


def test_device_check_reports_changed_refs_without_raising(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    report = review(tmp_path, {}, bridge, {'library': 'synthetic', 'device': 'cell0'})
    check = report['device_check']
    assert check['binding'] == {'status': 'ok', 'detail': None}
    # The base fixture has no simulation document, so the interface dependency is
    # not referenced by this device's documents (the same rule freshness uses).
    assert {row['ref']: row['status'] for row in check['refs']} == {
        'cell0_cdf': 'ok', 'cell0_symbol': 'ok'}
    assert report['status'] == 'ok'
    bridge.data['cdf']['parameters']['items'][0]['defValue']['value'] = '9u'
    report = review(tmp_path, {}, bridge, {'library': 'synthetic', 'device': 'cell0'})
    statuses = {row['ref']: row['status'] for row in report['device_check']['refs']}
    assert statuses['cell0_cdf'] == 'changed' and statuses['cell0_symbol'] == 'ok'
    assert report['status'] == 'incomplete'
    entry = next(row for row in report['impact']['changed'] if row['ref'] == 'cell0_cdf')
    assert entry['affected_devices'] == ['cell0']
    assert entry['detail'].startswith('Source changed')
    # The fail-closed gate shares the comparison and still refuses.
    from cadai.pdk_errors import PdkUnavailable
    from cadai.pdk_normalize import context, detail
    from cadai.pdk_standard.source_check import observed

    import pytest

    live = detail(bridge.capture('device', value['target'])['data'],
                  context(bridge.ctx))
    with pytest.raises(PdkUnavailable, match='Source changed'):
        observed(select(tmp_path, 'synthetic', {}), 'cell0', live,
                 ['cell0_cdf', 'cell0_symbol'])


def test_review_requires_a_bridge_for_device_checks(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    import pytest

    from cadai.pdk_errors import PdkUnavailable

    with pytest.raises(PdkUnavailable, match='bridge'):
        review(tmp_path, {}, None, {'library': 'synthetic', 'device': 'cell0'})

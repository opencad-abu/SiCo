"""Live dependency refreshes through the reviewed baseline rebase."""

import hashlib

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_normalize import context, detail
from cadai.pdk_standard.baseline import Baseline
from cadai.pdk_standard.resource_check import check as resource_check
from cadai.pdk_standard.source_check import observed
from cadai.pdk_standard.workspace import select
from pdk_standard_fixture import replace, update
from test_pdk_standard_impact import with_model_dependency


def digest(content):
    return 'sha256:' + hashlib.sha256(content).hexdigest()


def owner(workspace, bridge=None):
    instance = Baseline(workspace, {}, bridge)
    instance.confirmation = lambda _: {'decision': 'confirm', 'actor': 'test user',
                                       'evidence_ref': 'audit:test'}
    return instance


def test_changed_resource_refreshes_to_the_live_fingerprint(tmp_path):
    current, bridge, model = with_model_dependency(tmp_path)
    model.write_bytes(b'model drifted\n')
    instance = owner(tmp_path)
    preview = instance.prepare({'library': 'synthetic', 'revision': current.revision,
                                'refresh_resources': ['bytes_test']})
    row = preview['changes'][0]['changes'][0]
    assert row['path'] == '/dependencies/bytes_test/fingerprint'
    assert row['before'] == digest(b'model test\n') and row['after'] == digest(b'model drifted\n')
    scope = instance.reviews.read(preview['update_ref'])[1]
    assert scope['re_verification']['required']
    assert scope['re_verification']['documents'] >= 1
    result = instance.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'ok'
    assert result['refreshed'] == {'bytes_test': {'from': row['before'], 'to': row['after'],
                                                  'kind': 'model'}}
    final = select(tmp_path, 'synthetic', {})
    assert final.manifest['dependencies']['bytes_test']['fingerprint'] == row['after']
    resource_check(final, 'bytes_test')
    # A converged refresh reports that nothing changed any more.
    with pytest.raises(PdkUnavailable, match='No source changes'):
        owner(tmp_path).prepare({'library': 'synthetic', 'revision': final.revision,
                                 'refresh_resources': ['bytes_test']})


def test_refresh_reapproves_delta_changes_citing_the_dependency(tmp_path):
    current, bridge, model = with_model_dependency(tmp_path)
    updates, preview = update(tmp_path, current, [replace('/parameters/w/meaning', 'Width')],
                              file='cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    assert current.changes['cell0/cdf.json']['dependencies']['bytes_test'] == digest(b'model test\n')
    model.write_bytes(b'drifted after the review\n')
    instance = owner(tmp_path)
    preview = instance.prepare({'library': 'synthetic', 'revision': current.revision,
                                'refresh_resources': ['bytes_test']})
    scope = instance.reviews.read(preview['update_ref'])[1]
    assert scope['rebase']['reapproved_changes'] == ['cell0/cdf.json']
    result = instance.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'ok'
    assert result['reapproved']['changes'] == ['cell0/cdf.json']
    final = select(tmp_path, 'synthetic', {})
    assert final.changes['cell0/cdf.json']['dependencies']['bytes_test'] \
        == digest(b'drifted after the review\n')
    assert final.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'Width'
    record = result['rebase']['refreshed_dependencies']['bytes_test']
    assert record['from'] == digest(b'model test\n') and record['to'] == digest(
        b'drifted after the review\n')


def test_drift_between_prepare_and_apply_requires_a_new_review(tmp_path):
    current, bridge, model = with_model_dependency(tmp_path)
    model.write_bytes(b'first drift\n')
    instance = owner(tmp_path)
    preview = instance.prepare({'library': 'synthetic', 'revision': current.revision,
                                'refresh_resources': ['bytes_test']})
    model.write_bytes(b'second drift\n')
    with pytest.raises(PdkUnavailable, match='Source changed again'):
        instance.apply({'update_ref': preview['update_ref']})
    assert select(tmp_path, 'synthetic', {}).revision == current.revision


def test_device_refresh_recaptures_live_and_requires_the_bridge(tmp_path):
    from pdk_standard_fixture import package

    current, bridge, value, _ = package(tmp_path)
    bridge.data['cdf']['parameters']['items'][0]['defValue']['value'] = '9u'
    with pytest.raises(PdkUnavailable, match='bridge'):
        Baseline(tmp_path, {}).prepare({'library': 'synthetic', 'revision': current.revision,
                                        'refresh_devices': ['cell0']})
    instance = owner(tmp_path, bridge)
    preview = instance.prepare({'library': 'synthetic', 'revision': current.revision,
                                'refresh_devices': ['cell0']})
    rows = {row['path']: row for row in preview['changes'][0]['changes']}
    assert '/dependencies/cell0_cdf/fingerprint' in rows
    assert all('cell0_symbol' not in path for path in rows)
    result = instance.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'ok' and 'cell0_cdf' in result['refreshed']
    final = select(tmp_path, 'synthetic', {})
    capture = bridge.capture('device', value['target'])
    live = detail(capture['data'], context(capture['context']))
    observed(final, 'cell0', live, ['cell0_cdf', 'cell0_symbol'])

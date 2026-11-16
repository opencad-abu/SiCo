"""Reviewed baseline identity updates and the explicit single overlay rebase."""

import json

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.baseline import Baseline
from cadai.pdk_standard.workspace import select
from pdk_standard_fixture import package, replace, update

CONFIRM = {'decision': 'confirm', 'actor': 'test user', 'evidence_ref': 'audit:test'}


def overlay_candidate(workspace):
    """A synthetic package with one reviewed cumulative delta change."""
    current, _, _, _ = package(workspace)
    updates, preview = update(workspace, current,
                              [replace('/parameters/w/meaning', 'Width')],
                              file='cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    return select(workspace, 'synthetic', {})


def baseline(workspace, confirm=None):
    owner = Baseline(workspace, {})
    owner.confirmation = lambda _: dict(confirm or CONFIRM)
    return owner


def test_prepare_shows_the_identity_difference_and_the_required_rebase(tmp_path):
    current = overlay_candidate(tmp_path)
    owner = baseline(tmp_path)
    result = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                            'pdk_version': 'SPDK_TEST_V1.0'})
    assert result['confirmation_required']
    assert result['update_ref'].startswith('pdk-baseline:')
    group = result['changes'][0]
    assert group['file'] == 'package.json'
    assert group['changes'][0]['path'] == '/pdk_version'
    assert group['changes'][0]['before']['state'] == 'unknown'
    assert group['changes'][0]['after'] == 'SPDK_TEST_V1.0'
    scope = owner.reviews.read(result['update_ref'])[1]
    assert scope['kind'] == 'baseline'
    assert scope['rebase']['required'] and scope['rebase']['base_revision'] == current.base.revision
    assert scope['re_verification'] == {'required': False}


def test_apply_rebases_the_overlay_and_switches_both_entries(tmp_path):
    current = overlay_candidate(tmp_path)
    old_base, old_overlay = current.base, current.overlay
    assert old_overlay is not None
    owner = baseline(tmp_path)
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V1.0',
                             'options': {'configuration': '1P10M_8Ma_8Ic_2TMc_ALPA1'}})
    result = owner.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'ok'
    assert result['rebase']['from'] == {'revision': old_base.revision,
                                        'manifest_digest': old_base.manifest_digest}
    assert result['re_verification']['logical_documents_changed'] == 0
    assert result['re_verification']['logical_documents_compared'] > 0
    assert result['re_verification']['fingerprints_refreshed'] == 0
    final = select(tmp_path, 'synthetic', {})
    assert final.revision == result['revision'] != current.revision
    assert final.base.revision == result['baseline_revision']
    assert final.manifest['pdk_version'] == 'SPDK_TEST_V1.0'
    assert final.manifest['options'] == {'configuration': '1P10M_8Ma_8Ic_2TMc_ALPA1'}
    # The delta kept exactly its change and records the one explicit rebase.
    assert final.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'Width'
    record = json.loads((final.overlay / 'package.json').read_text())
    assert record['x_rebase']['from']['revision'] == old_base.revision
    assert record['base'] == {'package_id': 'synthetic', 'revision': final.base.revision,
                              'manifest_digest': final.base.manifest_digest}
    # Old directories stay immutable and addressable; the rebased delta is new.
    assert old_base.root.exists() and old_overlay.exists() and final.overlay != old_overlay
    # Reapplying returns the terminal receipt without re-asking the host.
    owner.confirmation = lambda _: pytest.fail('confirmation must not repeat')
    assert owner.apply({'update_ref': preview['update_ref']}) == result


def test_identity_update_without_overlay_points_the_index_at_the_new_baseline(tmp_path):
    current, _, _, _ = package(tmp_path)
    assert current.overlay is None
    owner = baseline(tmp_path)
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V2.0'})
    result = owner.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'ok' and result['rebase'] is None
    final = select(tmp_path, 'synthetic', {})
    assert final.overlay is None
    assert final.base.root != current.base.root
    assert final.manifest['pdk_version'] == 'SPDK_TEST_V2.0'


def test_same_identity_and_empty_requests_are_rejected(tmp_path):
    current = overlay_candidate(tmp_path)
    owner = baseline(tmp_path)
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V1.0'})
    owner.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    with pytest.raises(PdkUnavailable, match='already matches'):
        owner.prepare({'library': 'synthetic', 'revision': current.revision,
                       'pdk_version': 'SPDK_TEST_V1.0'})
    with pytest.raises(PdkUnavailable, match='No baseline identity'):
        owner.prepare({'library': 'synthetic', 'revision': current.revision})
    with pytest.raises(PdkUnavailable, match='nonempty mapping'):
        owner.prepare({'library': 'synthetic', 'revision': current.revision, 'options': {}})


def test_cancelled_confirmation_keeps_the_current_revision(tmp_path):
    current = overlay_candidate(tmp_path)
    owner = baseline(tmp_path, confirm={'decision': 'cancel', 'actor': 'test user',
                                        'evidence_ref': 'audit:test'})
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V1.0'})
    result = owner.apply({'update_ref': preview['update_ref']})
    assert result['status'] == 'cancelled'
    assert select(tmp_path, 'synthetic', {}).revision == current.revision
    owner.confirmation = lambda _: pytest.fail('confirmation must not repeat')
    assert owner.apply({'update_ref': preview['update_ref']})['status'] == 'cancelled'


def test_baseline_update_requires_a_workspace_index(tmp_path):
    owner = baseline(tmp_path)
    with pytest.raises(PdkUnavailable, match='workspace PDK data index') as error:
        owner.prepare({'library': 'synthetic', 'revision': 'missing',
                       'pdk_version': 'SPDK_TEST_V1.0'})
    assert error.value.code == 'pdk_standard_unavailable'


def test_concurrent_change_between_prepare_and_apply_conflicts(tmp_path):
    current = overlay_candidate(tmp_path)
    owner = baseline(tmp_path)
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V1.0'})
    updates, other = update(tmp_path, current,
                            [replace('/parameters/w/meaning', 'Width v2')],
                            file='cell0/cdf.json')
    updates.apply({'update_ref': other['update_ref']})
    with pytest.raises(PdkUnavailable, match='Concurrent PDK update'):
        owner.apply({'update_ref': preview['update_ref']})


def test_index_switch_failure_preserves_the_current_revision(tmp_path, monkeypatch):
    import cadai.pdk_standard.rebase as module

    current = overlay_candidate(tmp_path)
    owner = baseline(tmp_path)
    preview = owner.prepare({'library': 'synthetic', 'revision': current.revision,
                             'pdk_version': 'SPDK_TEST_V1.0'})

    def boom(*args, **kwargs):
        raise OSError('switch failed')

    monkeypatch.setattr(module, 'atomic', boom)
    with pytest.raises(OSError):
        owner.apply({'update_ref': preview['update_ref']})
    assert select(tmp_path, 'synthetic', {}).revision == current.revision


def test_baseline_service_is_available_without_a_workflow_mode(tmp_path):
    from cadai.pdk_standard.service import StandardData

    service = StandardData(tmp_path, {})
    assert service.baseline is not None
